# Resume Formatter - Streamlit App

A Streamlit application that reformats resumes into a standardized, professional format using Google Gemini AI.

## Features

- 📄 Upload PDF or DOCX resumes
- 🧠 AI-powered content extraction (preserves original text, doesn't summarize)
- 🎨 Beautiful, standardized formatting
- 📥 Download formatted PDF
- 🔒 Secure API key management

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

> **WeasyPrint on Windows:** The PDF renderer now depends on the GTK3 runtime. Install it once with:
> ```powershell
> winget install --id=GTK.GTK3 -e
> ```
> or download the installer from [WeasyPrint's Windows prerequisites](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows).

### 2. Configure API Key

Get your Google Gemini API key from: https://makersuite.google.com/app/apikey

**Option A: Using Streamlit Secrets (Recommended)**

Edit `.streamlit/secrets.toml` and add your API key:

```toml
GOOGLE_API_KEY = "your-actual-api-key-here"
```

**Option B: Enter in App**

Leave the secrets file as-is and enter your API key in the sidebar when running the app.

### 3. Run the Application

```bash
streamlit run app.py
```

The app will open in your browser at `http://localhost:8501`

## Usage

1. **Enter API Key** (if not using secrets file)
2. **Upload Resume** - Click "Browse files" and select your PDF or DOCX resume
3. **Process** - Click "🚀 Process Resume" button
4. **Download** - Click the download button to get your formatted resume

## Supported Sections

The app automatically detects and formats these sections:

- Professional Experience
- Education
- Certifications
- Skills
- Projects
- Publications
- Awards & Honors
- Volunteer Experience
- Professional Affiliations
- Languages

## Deployment on Streamlit Cloud

1. Push your code to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repository
4. Add your `GOOGLE_API_KEY` in the Streamlit Cloud secrets section
5. Deploy!

## Files

- `app.py` - Main Streamlit application
- `templatev2.html` - HTML template for PDF generation
- `ml-logo (1).png` - Company logo for PDF header
- `requirements.txt` - Python dependencies
- `.streamlit/secrets.toml` - API key storage (keep this private!)

## Customization

### Modify the Template

Edit `templatev2.html` to change the visual styling of the formatted resume.

### Adjust Extraction Logic

Edit the prompt in `parse_resume_with_gemini()` function in `app.py` to change what information is extracted.

## Troubleshooting

**API Key Error**: Make sure your API key is valid and has Gemini API access enabled.

**PDF Generation Error**: Ensure WeasyPrint and its native dependencies are installed (GTK3 runtime on Windows).

**File Upload Error**: Check that your file is a valid PDF or DOCX format.

**PDF Text Extraction Warning**: If you see a fallback warning, the app could not read layout metadata from the PDF and is using plain text instead. Convert the resume to a text-based PDF (e.g., export from Word/Google Docs or run OCR on scans) for best results.

## License

MIT License - Feel free to use and modify for your needs.

