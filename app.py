import os
import re
import json
import tempfile
from copy import deepcopy
from string import Template
from pathlib import Path
import streamlit as st
import pymupdf4llm
import docx
from jinja2 import Environment, FileSystemLoader
from weasyprint import HTML, CSS
import google.generativeai as genai
from pypdf import PdfWriter, PdfReader
from docx2pdf import convert

# --- Page Configuration ---
st.set_page_config(
    page_title="ML Resume Formatter",
    page_icon="📄",
    layout="wide"
)

# --- API Key Configuration ---
def get_api_key():
    """Get API key from secrets or user input."""
    api_key = None
    
    # Try to get from Streamlit secrets first
    try:
        api_key = st.secrets["GOOGLE_API_KEY"]
        if api_key and api_key != "your-api-key-here":
            return api_key
    except (KeyError, FileNotFoundError):
        pass
    
    # Fallback to sidebar input
    with st.sidebar:
        st.subheader("⚙️ Configuration")
        api_key = st.text_input(
            "Enter your Google Gemini API Key:",
            type="password",
            help="Get your API key from https://makersuite.google.com/app/apikey"
        )
    
    return api_key if api_key else None


# --- Text Extraction Functions ---
def extract_text_from_pdf(file_path):
    """Extract text from PDF using pymupdf4llm for better structure preservation."""
    try:
        # Use pymupdf4llm for better structure extraction
        text = pymupdf4llm.to_markdown(file_path)
        return text
    except Exception as e:
        st.error(f"Error extracting text from PDF: {e}")
        return None


def extract_text_from_docx(file_path):
    """Extract text from DOCX file."""
    try:
        doc = docx.Document(file_path)
        text = ""
        for para in doc.paragraphs:
            text += para.text + "\n"
        return text
    except Exception as e:
        st.error(f"Error extracting text from DOCX: {e}")
        return None


def extract_text_from_resume(file_path):
    """Main extraction function that routes to appropriate handler."""
    if file_path.lower().endswith('.pdf'):
        return extract_text_from_pdf(file_path)
    elif file_path.lower().endswith('.docx'):
        return extract_text_from_docx(file_path)
    else:
        st.error("Unsupported file format. Please upload PDF or DOCX.")
        return None


# --- Editing Helpers ---
def blank_experience():
    return {"title": "", "company": "", "dates": "", "description": []}


def blank_education():
    return {"degree": "", "institution": "", "dates": "", "details": []}


def blank_structured_entry():
    return {"title": "", "organization": "", "dates": "", "description": []}


def sanitize_multiline(text):
    if not text:
        return []
    if isinstance(text, list):
        return [line.strip() for line in text if isinstance(line, str) and line.strip()]
    return [line.strip() for line in str(text).splitlines() if line.strip()]


def safe_strip(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def reset_editor_widget_state():
    keys_to_clear = [key for key in st.session_state.keys() if any(
        key.startswith(prefix) for prefix in (
            "editor_", "exp_", "edu_", "section_"
        )
    )]
    for key in keys_to_clear:
        del st.session_state[key]


def clear_resume_processing_state():
    resume_keys = [
        "parsed_resume",
        "edited_resume",
        "parse_warnings",
        "generated_pdf",
        "edited_json",
        "validation_errors",
        "validation_warnings",
        "uploaded_filename",
        "candidate_sheet",
        "candidate_sheet_signature",
    ]
    for key in resume_keys:
        st.session_state.pop(key, None)
    reset_editor_widget_state()


def validate_resume_data(data):
    errors = []
    warnings = []

    if not isinstance(data, dict):
        errors.append("Parsed resume data is not in the expected format.")
        return errors, warnings

    # ===== NAME VALIDATION =====
    name = safe_strip(data.get("name", ""))
    if not name:
        errors.append("Candidate name is empty.")
    elif len(name) < 3:
        warnings.append("Name seems too short - is it complete?")
    elif name.isupper() and len(name) > 10:
        warnings.append("Name is in all caps - consider using proper case.")

    # ===== EXPERIENCE VALIDATION =====
    experience = data.get("experience", [])
    if not experience:
        errors.append("No experience entries provided.")
    else:
        for idx, entry in enumerate(experience, start=1):
            entry_data = entry or {}
            title = safe_strip(entry_data.get("title", ""))
            company = safe_strip(entry_data.get("company", ""))
            dates = safe_strip(entry_data.get("dates", ""))
            description = sanitize_multiline(entry_data.get("description", []))
            
            # Missing critical fields
            if not title and not company:
                errors.append(f"Experience #{idx} is missing both title and company.")
            if not title:
                warnings.append(f"Experience #{idx} is missing job title.")
            if not company:
                warnings.append(f"Experience #{idx} is missing company name.")
            if not dates:
                warnings.append(f"Experience #{idx} is missing dates.")
            
            # Description validation
            if not description:
                warnings.append(f"Experience #{idx} has no bullet points.")
            else:
                # Check for empty or very short bullets
                for bullet_idx, bullet in enumerate(description, start=1):
                    if len(bullet.strip()) < 10:
                        warnings.append(f"Experience #{idx}, bullet {bullet_idx} is too short (less than 10 characters).")
                    if has_problematic_characters(bullet):
                        warnings.append(f"Experience #{idx}, bullet {bullet_idx} contains special characters that may not render properly.")
        
        # Check for duplicate experiences
        if len(experience) > 1:
            for i in range(len(experience)):
                for j in range(i + 1, len(experience)):
                    if is_duplicate_entry(experience[i], experience[j]):
                        warnings.append(f"Experience #{i+1} and #{j+1} appear to be duplicates.")

    # ===== EDUCATION VALIDATION =====
    education = data.get("education", [])
    if not education:
        warnings.append("No education entries provided - consider adding your educational background.")
    else:
        for idx, entry in enumerate(education, start=1):
            entry_data = entry or {}
            degree = safe_strip(entry_data.get("degree", ""))
            institution = safe_strip(entry_data.get("institution", ""))
            dates = safe_strip(entry_data.get("dates", ""))
            
            if not any([degree, institution, dates]):
                errors.append(f"Education entry #{idx} is completely empty.")
            else:
                if not degree:
                    warnings.append(f"Education #{idx} is missing degree/credential.")
                if not institution:
                    warnings.append(f"Education #{idx} is missing institution name.")
                if not dates:
                    warnings.append(f"Education #{idx} is missing dates.")

    # ===== OTHER SECTIONS VALIDATION =====
    other_sections = data.get("other_sections", [])
    for sec_idx, section in enumerate(other_sections, start=1):
        section_title = safe_strip(section.get("section_title", ""))
        section_type = section.get("type", "")
        
        if not section_title:
            warnings.append(f"Additional section #{sec_idx} has no title.")
        
        if section_type == "structured":
            entries = section.get("entries", [])
            if not entries:
                warnings.append(f"Section '{section_title}' has no entries.")
            for entry_idx, entry in enumerate(entries, start=1):
                if not any([safe_strip(entry.get("title", "")), 
                           safe_strip(entry.get("organization", "")),
                           entry.get("description", [])]):
                    warnings.append(f"Section '{section_title}', entry #{entry_idx} is empty.")
        
        elif section_type == "list":
            items = section.get("items", [])
            if not items:
                warnings.append(f"Section '{section_title}' has no items.")
            for item_idx, item in enumerate(items, start=1):
                if len(safe_strip(item)) < 3:
                    warnings.append(f"Section '{section_title}', item #{item_idx} is too short.")

    return errors, warnings


def has_problematic_characters(text):
    """Check for characters that might not render well in PDF."""
    # Check for corrupted encoding artifacts
    problematic_patterns = [
        r'â€',  # Common corruption pattern
        r'[\x00-\x08\x0B\x0C\x0E-\x1F]',  # Control characters
        r'�',  # Replacement character (indicates encoding issues)
    ]
    
    for pattern in problematic_patterns:
        if re.search(pattern, text):
            return True
    return False


def is_duplicate_entry(entry1, entry2):
    """Check if two experience/education entries are duplicates."""
    # Compare key fields
    def get_signature(entry):
        return (
            safe_strip(entry.get("title", "")).lower(),
            safe_strip(entry.get("company", "")).lower(),
            safe_strip(entry.get("degree", "")).lower(),
            safe_strip(entry.get("institution", "")).lower(),
        )
    
    sig1 = get_signature(entry1)
    sig2 = get_signature(entry2)
    
    # If at least 2 fields match exactly, likely duplicate
    matches = sum(1 for a, b in zip(sig1, sig2) if a and b and a == b)
    return matches >= 2


# --- LLM Parser ---
def parse_resume_with_gemini(text, api_key):
    """Parse resume text using Gemini LLM to extract structured data without summarizing."""
    
    # Configure Gemini
    genai.configure(api_key=api_key)
    
    # Clean encoding issues BEFORE sending to LLM
    def clean_encoding(text):
        if isinstance(text, str):
            text = text.replace('–', '-')  # en-dash
            text = text.replace('—', '-')  # em-dash
            text = text.replace('"', '"').replace('"', '"')  # smart quotes
            text = text.replace(''', "'").replace(''', "'")  # smart apostrophes
            text = text.replace('…', '...')  # ellipsis
            text = text.replace('â€"', '-')  # corrupted en-dash
            text = text.replace('â€œ', '"').replace('â€', '"')  # corrupted quotes
            text = text.replace('â€™', "'").replace('â€˜', "'")  # corrupted apostrophes
            text = text.replace('â€¢', '•')  # corrupted bullet
            text = text.replace('â€¦', '...')  # corrupted ellipsis
            # Normalize subscripts/superscripts (not supported by xhtml2pdf)
            subscripts = str.maketrans('₀₁₂₃₄₅₆₇₈₉', '0123456789')
            superscripts = str.maketrans('⁰¹²³⁴⁵⁶⁷⁸⁹', '0123456789')
            text = text.translate(subscripts).translate(superscripts)
        return text
    
    text = clean_encoding(text)
    
    # Create the extraction prompt - emphasis on PRESERVING exact content
    prompt_template = Template("""
You are an expert resume parser. Produce a single JSON object that mirrors the resume content without inventing information.

PRIMARY GOALS
1. Preserve wording exactly; do not summarize, paraphrase, or translate.
2. Normalize formatting only—remove pipes, duplicate spaces, leading bullets, and line breaks that split sentences.
3. Capture every section that delivers meaningful content.

OUTPUT RULES
- Return JSON only. No markdown fences, comments, or natural-language chatter.
- Use UTF-8 characters; replace corrupted symbols (â€“, â€™, etc.) with intended ASCII equivalents.
- If a value is unknown, omit the field; never return null.

SCHEMA
{
  "name": "<full name from resume header>",
  "email": "<primary email if present>",
  "phone": "<primary phone number if present>",
  "location": "<primary location or city if present>",
  "linkedin": "<primary LinkedIn URL if present>",
  "website": "<primary website or portfolio if present>",
  "github": "<GitHub URL if present>",
  "experience": [
    {
      "title": "<exact job title>",
      "company": "<company name with location if present>",
      "dates": "<verbatim date range>",
      "description": ["<bullet point 1>", "<bullet point 2>", …]
    }
  ],
  "education": [
    {
      "degree": "<degree name>",
      "institution": "<institution name with location if present>",
      "dates": "<verbatim dates>",
      "details": ["<detail 1>", …]
    }
  ],
  "other_sections": [
    {
      "section_title": "<exact section heading>",
      "type": "structured",
      "entries": [
        {
          "title": "<role/project/activity>",
          "organization": "<organization if given>",
          "dates": "<verbatim dates>",
          "description": ["<bullet>", …]
        }
      ]
    },
    {
      "section_title": "<exact section heading>",
      "type": "list",
      "items": ["<item>", …]
    }
  ]
}

INSTRUCTIONS
- Classify each section as `structured` if items contain titles/roles; otherwise use `list`.
- Combine organization and location inside the same string separated by a comma.
- Include every distinct contact detail line in `contact_details` in the order encountered near the top of the resume.
- Populate dedicated contact fields (`email`, `phone`, `location`, `linkedin`, `website`, `github`) when available; leave them out if not found.
- Merge multi-line bullets into single strings when they form one sentence or idea.
- Preserve order of sections and entries as they appear.
- Keep acronyms and capitalization exactly as given.
- For overlapping roles at the same company, output separate entries.
- If the resume lacks a recognized section, omit the array entirely.
- If you detect parsing uncertainty (e.g., ambiguous tables, duplicate sections), include `"_warnings": ["<detailed note>"]`. Be specific about which entries or sections are problematic, referencing them by position (e.g., "First CAREER EXPERIENCE section at top vs. second at bottom") or by job title/company name. Otherwise output `"_warnings": []`.
- Finish with `"version": "v2"` to signal the schema version.

Resume text:
---
$resume_text
---
""")

    prompt = prompt_template.substitute(resume_text=text)
    
    try:
        with st.spinner("🧠 Parsing resume with Gemini AI..."):
            model = genai.GenerativeModel('gemini-2.5-pro')
            generation_config = genai.types.GenerationConfig(
                temperature=0  # Deterministic output for consistency
            )
            response = model.generate_content(
                prompt, 
                generation_config=generation_config,
                request_options={"timeout": 120}
            )
            
            response_text = response.text.strip()
            
            # Remove markdown code fences if present
            if response_text.startswith("```json"):
                match = re.search(r'```json\n(.*?)\n```', response_text, re.DOTALL)
                if match:
                    response_text = match.group(1)
            elif response_text.startswith("```"):
                match = re.search(r'```\n(.*?)\n```', response_text, re.DOTALL)
                if match:
                    response_text = match.group(1)
            
            # Parse JSON
            parsed_data = json.loads(response_text)
            
            # Simple cleanup of any remaining artifacts
            def clean_text(text):
                if isinstance(text, str):
                    text = text.strip()
                    # Remove any remaining leading dashes/bullets
                    text = text.lstrip('- •·∙→▪')
                    text = text.strip()
                return text
            
            def clean_dict(obj):
                if isinstance(obj, dict):
                    return {k: clean_dict(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_dict(item) for item in obj]
                elif isinstance(obj, str):
                    return clean_text(obj)
                return obj
            
            parsed_data = clean_dict(parsed_data)
            
            # Log the parsed data for debugging
            with st.expander("🔍 View Extracted Data (Debug)", expanded=False):
                st.json(parsed_data)
            
            return parsed_data
            
    except json.JSONDecodeError as e:
        st.error(f"❌ Failed to parse JSON response from Gemini")
        with st.expander("📋 Error Details", expanded=True):
            st.error(f"JSON Error: {e}")
            st.code(response_text, language="text")
        return None
        
    except Exception as e:
        st.error(f"❌ Error calling Gemini API: {e}")
        with st.expander("📋 Error Details", expanded=True):
            st.exception(e)
        return None


# --- PDF Generation ---
def generate_pdf(data, output_path):
    """Generate formatted PDF from parsed data using HTML template."""
    try:
        with st.spinner("📄 Generating formatted PDF..."):
            # Get the directory where this script is located
            template_dir = os.path.dirname(os.path.abspath(__file__))
            
            # Load template
            env = Environment(loader=FileSystemLoader(template_dir), autoescape=True)
            template = env.get_template("templatev2.html")
            
            # Render HTML with absolute logo path
            logo_file = Path(template_dir, 'ml-logo (1).png')
            logo_uri = logo_file.resolve().as_uri() if logo_file.exists() else ''
            data_with_logo = {**data, 'logo_path': logo_uri}
            html_content = template.render(data_with_logo)
            
            # Generate PDF using WeasyPrint (better CSS support)
            html = HTML(string=html_content, base_url=template_dir)
            html.write_pdf(target=output_path)
            
            return True
            
    except Exception as e:
        st.error(f"❌ Error generating PDF: {e}")
        with st.expander("📋 Error Details", expanded=True):
            st.exception(e)
        return False


def convert_docx_to_pdf(docx_path, pdf_path):
    """Convert DOCX file to PDF using docx2pdf."""
    try:
        convert(docx_path, pdf_path)
        return True
    except Exception as e:
        st.error(f"❌ Error converting DOCX to PDF: {e}")
        with st.expander("📋 Error Details", expanded=True):
            st.exception(e)
        return False


def merge_pdfs(main_pdf_path, candidate_sheet_path, output_path):
    """Merge two PDFs - append candidate sheet to the end of main resume."""
    try:
        pdf_writer = PdfWriter()
        
        # Add all pages from main resume
        with open(main_pdf_path, 'rb') as main_file:
            main_pdf = PdfReader(main_file)
            for page in main_pdf.pages:
                pdf_writer.add_page(page)
        
        # Add all pages from candidate sheet
        with open(candidate_sheet_path, 'rb') as candidate_file:
            candidate_pdf = PdfReader(candidate_file)
            for page in candidate_pdf.pages:
                pdf_writer.add_page(page)
        
        # Write merged PDF
        with open(output_path, 'wb') as output_file:
            pdf_writer.write(output_file)
        
        return True
    except Exception as e:
        st.error(f"❌ Error merging PDFs: {e}")
        with st.expander("📋 Error Details", expanded=True):
            st.exception(e)
        return False


# --- Main Application ---
def main():
    st.title("📄 Resume Formatter")
    st.markdown("""
    Transform your resume into a standardized, professional format.
    
    **How it works:**
    1. Upload your resume (PDF or DOCX)
    2. AI extracts and preserves your content
    3. Download your beautifully formatted resume
    """)
    
    st.session_state.setdefault("parsed_resume", None)
    st.session_state.setdefault("edited_resume", None)
    st.session_state.setdefault("parse_warnings", [])
    st.session_state.setdefault("generated_pdf", None)
    st.session_state.setdefault("edited_json", None)
    st.session_state.setdefault("validation_errors", [])
    st.session_state.setdefault("validation_warnings", [])
    st.session_state.setdefault("uploaded_file_signature", None)
    st.session_state.setdefault("candidate_sheet", None)
    st.session_state.setdefault("candidate_sheet_signature", None)

    # Get API key
    api_key = get_api_key()
    
    if not api_key:
        st.warning("⚠️ Please enter your Google Gemini API key to continue.")
        st.info("Get your API key from: https://makersuite.google.com/app/apikey")
        st.stop()
    
    # File uploaders
    st.divider()
    uploaded_file = st.file_uploader(
        "Upload your resume",
        type=['pdf', 'docx'],
        help="Supported formats: PDF, DOCX"
    )
    
    candidate_sheet_file = st.file_uploader(
        "Upload Candidate Sheet (optional)",
        type=['pdf', 'docx'],
        help="Optional: Add an additional document to append after your resume"
    )

    if uploaded_file is not None:
        file_signature = f"{uploaded_file.name}:{getattr(uploaded_file, 'size', None)}"
        previous_signature = st.session_state.get("uploaded_file_signature")
        if file_signature != previous_signature:
            clear_resume_processing_state()
            st.session_state["uploaded_file_signature"] = file_signature
    else:
        if st.session_state.get("uploaded_file_signature") is not None:
            clear_resume_processing_state()
            st.session_state["uploaded_file_signature"] = None
    
    # Handle candidate sheet file state
    if candidate_sheet_file is not None:
        candidate_signature = f"{candidate_sheet_file.name}:{getattr(candidate_sheet_file, 'size', None)}"
        previous_candidate_signature = st.session_state.get("candidate_sheet_signature")
        if candidate_signature != previous_candidate_signature:
            st.session_state["candidate_sheet"] = candidate_sheet_file
            st.session_state["candidate_sheet_signature"] = candidate_signature
            # Clear generated PDF so user needs to regenerate with new candidate sheet
            st.session_state["generated_pdf"] = None
    else:
        if st.session_state.get("candidate_sheet_signature") is not None:
            st.session_state["candidate_sheet"] = None
            st.session_state["candidate_sheet_signature"] = None
            st.session_state["generated_pdf"] = None
    
    if uploaded_file is not None:
        # Display file info
        col1, col2 = st.columns([3, 1])
        with col1:
            st.success(f"✅ File uploaded: {uploaded_file.name}")
            if candidate_sheet_file is not None:
                st.info(f"📎 Candidate sheet: {candidate_sheet_file.name}")
        with col2:
            process_button = st.button("🚀 Process Resume", type="primary", use_container_width=True)
        
        if process_button:
            # Create temporary file to save upload
            with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(uploaded_file.name)[1]) as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name
            
            try:
                # Step 1: Extract text
                with st.status("Processing resume...", expanded=True) as status:
                    st.write("📄 Extracting text from resume...")
                    raw_text = extract_text_from_resume(tmp_path)
                    
                    if not raw_text:
                        st.error("Failed to extract text from resume.")
                        status.update(label="❌ Processing failed", state="error")
                        return
                    
                    st.write("✅ Text extracted successfully")
                    
                    # Show extracted raw text for debugging
                    with st.expander("📝 View Extracted Text (What the LLM sees)", expanded=False):
                        st.text_area("Raw Text from PDF/DOCX:", raw_text, height=300)
                    
                    # Step 2: Parse with Gemini
                    st.write("🧠 Parsing resume structure...")
                    parsed_data = parse_resume_with_gemini(raw_text, api_key)
                    
                    if not parsed_data:
                        st.error("Failed to parse resume data.")
                        status.update(label="❌ Processing failed", state="error")
                        return
                    
                    st.write("✅ Resume parsed successfully")
                    parsed_snapshot = deepcopy(parsed_data) if isinstance(parsed_data, dict) else parsed_data
                    model_warnings = []
                    if isinstance(parsed_snapshot, dict):
                        model_warnings = parsed_snapshot.pop("_warnings", [])
                    st.session_state["parse_warnings"] = model_warnings
                    reset_editor_widget_state()
                    if isinstance(parsed_snapshot, dict):
                        st.session_state["parsed_resume"] = deepcopy(parsed_snapshot)
                        st.session_state["edited_resume"] = deepcopy(parsed_snapshot)
                        st.session_state["edited_json"] = json.dumps(parsed_snapshot, indent=2, ensure_ascii=False)
                        errors, warnings = validate_resume_data(parsed_snapshot)
                        st.session_state["validation_errors"] = errors
                        st.session_state["validation_warnings"] = warnings
                    else:
                        st.session_state["parsed_resume"] = parsed_snapshot
                        st.session_state["edited_resume"] = parsed_snapshot
                        st.session_state["edited_json"] = json.dumps(parsed_data, indent=2, ensure_ascii=False)
                        st.session_state["validation_errors"] = []
                        st.session_state["validation_warnings"] = []
                    st.session_state["generated_pdf"] = None
                    st.session_state["uploaded_filename"] = uploaded_file.name
                    st.success("Review the parsed data below before generating the PDF.")
                    status.update(label="✅ Parsing complete — review the details below", state="complete")
                
            finally:
                # Cleanup temporary input file
                try:
                    os.unlink(tmp_path)
                except:
                    pass
    
    edited_resume_data = st.session_state.get("edited_resume")

    if edited_resume_data is not None:
        st.divider()
        st.subheader("Step 3: Review & edit details")

        if not isinstance(edited_resume_data, dict):
            st.warning("Parsed data is not in the expected object format. Please inspect the raw output in the debug panel.")
        else:
            warnings = st.session_state.get("parse_warnings", [])
            if warnings:
                st.warning("Gemini flagged the following items for review:")
                for warning in warnings:
                    st.markdown(f"- {warning}")

            st.caption("Adjust any fields below. Leave an entry blank to remove it when you save.")

            add_cols = st.columns(2)
            with add_cols[0]:
                if st.button("➕ Add experience entry"):
                    current = deepcopy(edited_resume_data)
                    current.setdefault("experience", []).append(blank_experience())
                    st.session_state["edited_resume"] = current
                    st.rerun()
            with add_cols[1]:
                if st.button("➕ Add education entry"):
                    current = deepcopy(edited_resume_data)
                    current.setdefault("education", []).append(blank_education())
                    st.session_state["edited_resume"] = current
                    st.rerun()

            name_value = st.text_input(
                "Candidate name",
                value=edited_resume_data.get("name", ""),
                key="editor_name"
            )

            version_value = st.text_input(
                "Schema version",
                value=edited_resume_data.get("version", ""),
                key="editor_version"
            )

            contact_cols = st.columns(2)
            with contact_cols[0]:
                email_value = st.text_input(
                    "Email",
                    value=edited_resume_data.get("email", ""),
                    key="contact_email"
                )
                phone_value = st.text_input(
                    "Phone",
                    value=edited_resume_data.get("phone", ""),
                    key="contact_phone"
                )
            mobile_value = st.text_input(
                "Alternate phone",
                value=edited_resume_data.get("mobile", ""),
                key="contact_mobile"
            )
            with contact_cols[1]:
                location_value = st.text_input(
                    "Location",
                    value=edited_resume_data.get("location", ""),
                    key="contact_location"
                )
                linkedin_value = st.text_input(
                    "LinkedIn URL",
                    value=edited_resume_data.get("linkedin", ""),
                    key="contact_linkedin"
                )
                website_value = st.text_input(
                    "Website / Portfolio",
                    value=edited_resume_data.get("website", ""),
                    key="contact_website"
                )

            github_value = st.text_input(
                "GitHub",
                value=edited_resume_data.get("github", ""),
                key="contact_github"
            )

            contact_details_value = st.text_area(
                "Additional Information (one per line)",
                value="\n".join(edited_resume_data.get("contact_details", []) or []),
                key="contact_details",
                height=80,
                help="Add custom contact details or other information to appear in the header (e.g., nationality, citizenship, portfolio links)"
            )

            experience_inputs = []
            for idx, job in enumerate(edited_resume_data.get("experience", []) or []):
                label = job.get("title") or job.get("company") or f"Experience #{idx + 1}"
                with st.expander(f"Experience #{idx + 1}: {label}", expanded=False):
                    title_value = st.text_input(
                        "Title",
                        value=job.get("title", ""),
                        key=f"exp_title_{idx}"
                    )
                    company_value = st.text_input(
                        "Company",
                        value=job.get("company", ""),
                        key=f"exp_company_{idx}"
                    )
                    dates_value = st.text_input(
                        "Dates",
                        value=job.get("dates", ""),
                        key=f"exp_dates_{idx}"
                    )
                    description_value = st.text_area(
                        "Description (one bullet per line)",
                        value="\n".join(job.get("description", []) or []),
                        key=f"exp_description_{idx}",
                        height=140
                    )
                    remove_value = st.checkbox(
                        "Remove this experience",
                        value=False,
                        key=f"exp_remove_{idx}"
                    )
                    experience_inputs.append({
                        "title": title_value,
                        "company": company_value,
                        "dates": dates_value,
                        "description_text": description_value,
                        "remove": remove_value,
                    })

            education_inputs = []
            for idx, edu in enumerate(edited_resume_data.get("education", []) or []):
                label = edu.get("degree") or edu.get("institution") or f"Education #{idx + 1}"
                with st.expander(f"Education #{idx + 1}: {label}", expanded=False):
                    degree_value = st.text_input(
                        "Degree",
                        value=edu.get("degree", ""),
                        key=f"edu_degree_{idx}"
                    )
                    institution_value = st.text_input(
                        "Institution",
                        value=edu.get("institution", ""),
                        key=f"edu_institution_{idx}"
                    )
                    edu_dates_value = st.text_input(
                        "Dates",
                        value=edu.get("dates", ""),
                        key=f"edu_dates_{idx}"
                    )
                    details_value = st.text_area(
                        "Details (one per line)",
                        value="\n".join(edu.get("details", []) or []),
                        key=f"edu_details_{idx}",
                        height=120
                    )
                    remove_education = st.checkbox(
                        "Remove this education entry",
                        value=False,
                        key=f"edu_remove_{idx}"
                    )
                    education_inputs.append({
                        "degree": degree_value,
                        "institution": institution_value,
                        "dates": edu_dates_value,
                        "details_text": details_value,
                        "remove": remove_education,
                    })

            other_sections_inputs = []
            for sec_idx, section in enumerate(edited_resume_data.get("other_sections", []) or []):
                section_title = section.get("section_title", "")
                with st.expander(f"Additional Section #{sec_idx + 1}: {section_title or 'Untitled'}", expanded=False):
                    section_title_value = st.text_input(
                        "Section title",
                        value=section_title,
                        key=f"section_title_{sec_idx}"
                    )
                    section_type_value = st.selectbox(
                        "Section type",
                        options=["structured", "list"],
                        index=0 if section.get("type", "structured") == "structured" else 1,
                        key=f"section_type_{sec_idx}"
                    )
                    remove_section = st.checkbox(
                        "Remove this section",
                        value=False,
                        key=f"section_remove_{sec_idx}"
                    )

                    entries_input = []
                    items_input = ""
                    if section_type_value == "structured":
                        if st.button("➕ Add entry", key=f"add_structured_entry_{sec_idx}"):
                            current = deepcopy(edited_resume_data)
                            current.setdefault("other_sections", [])
                            if sec_idx < len(current["other_sections"]):
                                current["other_sections"][sec_idx].setdefault("entries", []).append(blank_structured_entry())
                                st.session_state["edited_resume"] = current
                                st.rerun()
                        for entry_idx, entry in enumerate(section.get("entries", []) or []):
                            st.markdown(f"**Entry {entry_idx + 1}**")
                            entry_title_value = st.text_input(
                                "Title",
                                value=entry.get("title", ""),
                                key=f"section_{sec_idx}_entry_title_{entry_idx}"
                            )
                            entry_org_value = st.text_input(
                                "Organization",
                                value=entry.get("organization", ""),
                                key=f"section_{sec_idx}_entry_org_{entry_idx}"
                            )
                            entry_dates_value = st.text_input(
                                "Dates",
                                value=entry.get("dates", ""),
                                key=f"section_{sec_idx}_entry_dates_{entry_idx}"
                            )
                            entry_desc_value = st.text_area(
                                "Description (one per line)",
                                value="\n".join(entry.get("description", []) or []),
                                key=f"section_{sec_idx}_entry_desc_{entry_idx}",
                                height=120
                            )
                            entry_remove = st.checkbox(
                                "Remove this entry",
                                value=False,
                                key=f"section_{sec_idx}_entry_remove_{entry_idx}"
                            )
                            entries_input.append({
                                "title": entry_title_value,
                                "organization": entry_org_value,
                                "dates": entry_dates_value,
                                "description_text": entry_desc_value,
                                "remove": entry_remove,
                            })
                    else:
                        items_input = st.text_area(
                            "Items (one per line)",
                            value="\n".join(section.get("items", []) or []),
                            key=f"section_{sec_idx}_items",
                            height=120
                        )

                    other_sections_inputs.append({
                        "section_title": section_title_value,
                        "section_type": section_type_value,
                        "remove": remove_section,
                        "entries_input": entries_input,
                        "items_input": items_input,
                    })

            sanitized_experience = []
            for entry in experience_inputs:
                if entry.get("remove"):
                    continue
                cleaned_entry = {
                    "title": entry.get("title", "").strip(),
                    "company": entry.get("company", "").strip(),
                    "dates": entry.get("dates", "").strip(),
                    "description": sanitize_multiline(entry.get("description_text", "")),
                }
                if any([cleaned_entry["title"], cleaned_entry["company"], cleaned_entry["dates"], cleaned_entry["description"]]):
                    sanitized_experience.append(cleaned_entry)

            sanitized_education = []
            for entry in education_inputs:
                if entry.get("remove"):
                    continue
                cleaned_entry = {
                    "degree": entry.get("degree", "").strip(),
                    "institution": entry.get("institution", "").strip(),
                    "dates": entry.get("dates", "").strip(),
                    "details": sanitize_multiline(entry.get("details_text", ""))
                }
                if any([cleaned_entry["degree"], cleaned_entry["institution"], cleaned_entry["dates"], cleaned_entry["details"]]):
                    sanitized_education.append(cleaned_entry)

            sanitized_other_sections = []
            for section_input in other_sections_inputs:
                if section_input.get("remove"):
                    continue
                title_clean = section_input.get("section_title", "").strip()
                if section_input.get("section_type") == "structured":
                    entries_clean = []
                    for entry in section_input.get("entries_input", []):
                        if entry.get("remove"):
                            continue
                        cleaned_entry = {
                            "title": entry.get("title", "").strip(),
                            "organization": entry.get("organization", "").strip(),
                            "dates": entry.get("dates", "").strip(),
                            "description": sanitize_multiline(entry.get("description_text", ""))
                        }
                        if any([cleaned_entry["title"], cleaned_entry["organization"], cleaned_entry["dates"], cleaned_entry["description"]]):
                            entries_clean.append(cleaned_entry)
                    if title_clean or entries_clean:
                        sanitized_other_sections.append({
                            "section_title": title_clean or "Untitled Section",
                            "type": "structured",
                            "entries": entries_clean
                        })
                else:
                    items_clean = sanitize_multiline(section_input.get("items_input", ""))
                    if title_clean or items_clean:
                        sanitized_other_sections.append({
                            "section_title": title_clean or "Untitled Section",
                            "type": "list",
                            "items": items_clean
                        })

            contact_details_clean = sanitize_multiline(contact_details_value)
            contact_fields_raw = {
                "email": email_value.strip(),
                "phone": phone_value.strip(),
                "mobile": mobile_value.strip(),
                "location": location_value.strip(),
                "linkedin": linkedin_value.strip(),
                "website": website_value.strip(),
                "github": github_value.strip(),
            }
            contact_fields = {k: v for k, v in contact_fields_raw.items() if v}

            preserved_fields = {}
            excluded_keys = {
                "name", "experience", "education", "other_sections", "version",
                "email", "phone", "mobile", "location",
                "linkedin", "website", "github",
                "contact_details"
            }
            for key, value in edited_resume_data.items():
                if key not in excluded_keys:
                    preserved_fields[key] = deepcopy(value)

            updated_resume = {**preserved_fields}
            updated_resume["name"] = name_value.strip()
            updated_resume["experience"] = sanitized_experience
            updated_resume["education"] = sanitized_education
            if sanitized_other_sections:
                updated_resume["other_sections"] = sanitized_other_sections
            else:
                updated_resume["other_sections"] = []

            for key, value in contact_fields.items():
                updated_resume[key] = value

            if contact_details_clean:
                updated_resume["contact_details"] = contact_details_clean
            else:
                updated_resume.pop("contact_details", None)

            version_clean = version_value.strip()
            if version_clean:
                updated_resume["version"] = version_clean
            elif "version" in updated_resume:
                updated_resume.pop("version")

            validation_errors, validation_warnings = validate_resume_data(updated_resume)

            if validation_errors:
                st.error("Please address the following before generating:")
                for issue in validation_errors:
                    st.markdown(f"- {issue}")

            if validation_warnings:
                st.warning("You can continue, but review these items:")
                for warning in validation_warnings:
                    st.markdown(f"- {warning}")

            if not validation_errors and not validation_warnings:
                st.success("All required sections look good. You can generate the PDF when ready.")
            elif not validation_errors and validation_warnings:
                st.info("No blocking issues detected. The warnings above are optional but recommended to address.")

            action_cols = st.columns(2)
            with action_cols[0]:
                if st.button("💾 Save edits", key="save_edits"):
                    st.session_state["edited_resume"] = deepcopy(updated_resume)
                    st.session_state["edited_json"] = json.dumps(updated_resume, indent=2, ensure_ascii=False)
                    st.session_state["validation_errors"] = validation_errors
                    st.session_state["validation_warnings"] = validation_warnings
                    st.success("Edits saved to session.")
            with action_cols[1]:
                generate_disabled = bool(validation_errors)
                if st.button("✅ Confirm & Generate PDF", type="primary", key="generate_pdf_button", disabled=generate_disabled):
                    st.session_state["edited_resume"] = deepcopy(updated_resume)
                    st.session_state["edited_json"] = json.dumps(updated_resume, indent=2, ensure_ascii=False)
                    st.session_state["validation_errors"] = validation_errors
                    st.session_state["validation_warnings"] = validation_warnings
                    output_path = None
                    candidate_temp_pdf = None
                    candidate_temp_docx = None
                    final_output_path = None
                    try:
                        # Generate main resume PDF
                        with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_output:
                            output_path = tmp_output.name
                        
                        if generate_pdf(updated_resume, output_path):
                            # Check if there's a candidate sheet to append
                            candidate_sheet = st.session_state.get("candidate_sheet")
                            
                            if candidate_sheet is not None:
                                # Save candidate sheet to temporary file
                                file_ext = os.path.splitext(candidate_sheet.name)[1].lower()
                                
                                if file_ext == '.docx':
                                    # Save DOCX and convert to PDF
                                    with tempfile.NamedTemporaryFile(delete=False, suffix='.docx') as tmp_docx:
                                        tmp_docx.write(candidate_sheet.getvalue())
                                        candidate_temp_docx = tmp_docx.name
                                    
                                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                                        candidate_temp_pdf = tmp_pdf.name
                                    
                                    with st.spinner("Converting candidate sheet to PDF..."):
                                        if not convert_docx_to_pdf(candidate_temp_docx, candidate_temp_pdf):
                                            st.warning("Failed to convert candidate sheet. Proceeding with resume only.")
                                            candidate_temp_pdf = None
                                
                                elif file_ext == '.pdf':
                                    # Save PDF directly
                                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_pdf:
                                        tmp_pdf.write(candidate_sheet.getvalue())
                                        candidate_temp_pdf = tmp_pdf.name
                                
                                # Merge PDFs if candidate sheet was successfully processed
                                if candidate_temp_pdf:
                                    with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp_merged:
                                        final_output_path = tmp_merged.name
                                    
                                    with st.spinner("Merging resume with candidate sheet..."):
                                        if merge_pdfs(output_path, candidate_temp_pdf, final_output_path):
                                            # Read merged PDF
                                            with open(final_output_path, 'rb') as pdf_file:
                                                pdf_data = pdf_file.read()
                                        else:
                                            st.warning("Failed to merge candidate sheet. Proceeding with resume only.")
                                            with open(output_path, 'rb') as pdf_file:
                                                pdf_data = pdf_file.read()
                                else:
                                    # No candidate sheet or conversion failed, use main resume only
                                    with open(output_path, 'rb') as pdf_file:
                                        pdf_data = pdf_file.read()
                            else:
                                # No candidate sheet, use main resume only
                                with open(output_path, 'rb') as pdf_file:
                                    pdf_data = pdf_file.read()
                            
                            name_for_filename = updated_resume.get('name', 'Resume')
                            safe_name = "".join(c for c in name_for_filename if c.isalnum() or c in (' ', '_')).rstrip()
                            if not safe_name:
                                safe_name = 'Resume'
                            pdf_filename = f"Formatted_{safe_name.replace(' ', '_')}.pdf"
                            st.session_state["generated_pdf"] = {
                                "data": pdf_data,
                                "filename": pdf_filename,
                            }
                            st.success("PDF generated successfully. Download it below.")
                        else:
                            st.error("Failed to generate PDF. Please review the data and try again.")
                    finally:
                        # Cleanup temporary files
                        for temp_file in [output_path, candidate_temp_pdf, candidate_temp_docx, final_output_path]:
                            if temp_file and os.path.exists(temp_file):
                                try:
                                    os.unlink(temp_file)
                                except OSError:
                                    pass

            generated_pdf = st.session_state.get("generated_pdf")
            if generated_pdf:
                st.divider()
                st.success("🎉 Your formatted resume is ready!")
                download_cols = st.columns(2)
                with download_cols[0]:
                    st.download_button(
                        label="⬇️ Download Formatted Resume",
                        data=generated_pdf.get("data"),
                        file_name=generated_pdf.get("filename", "Formatted_Resume.pdf"),
                        mime="application/pdf",
                        use_container_width=True
                    )
                with download_cols[1]:
                    edited_json = st.session_state.get("edited_json")
                    if edited_json:
                        json_filename = generated_pdf.get("filename", "Formatted_Resume.pdf").replace('.pdf', '.json')
                        st.download_button(
                            label="⬇️ Download Edited JSON",
                            data=edited_json,
                            file_name=json_filename,
                            mime="application/json",
                            use_container_width=True
                        )

    # Footer
    st.divider()
    st.markdown("""
    <div style='text-align: center; color: #666; padding: 20px;'>
        <small>Powered by Google Gemini AI | Built with Streamlit</small>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()

