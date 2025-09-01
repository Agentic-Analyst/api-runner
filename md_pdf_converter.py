
from fastapi import HTTPException
import markdown
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from io import BytesIO
import re

def convert_md_to_pdf(md_content, ticker):
    html_content = markdown.markdown(md_content, output_format="html5")

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1*inch, bottomMargin=1*inch,
                            leftMargin=1*inch, rightMargin=1*inch)
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('CustomTitle', parent=styles['Title'],
                                    fontSize=24, spaceAfter=30, textColor='darkblue')
    heading_style = ParagraphStyle('CustomHeading', parent=styles['Heading1'],
                                    fontSize=16, spaceAfter=12, textColor='darkblue')

    story = []
    story.append(Paragraph(f"{ticker} Stock Analysis Report", title_style))
    story.append(Spacer(1, 20))

    lines = html_content.split('\n')
    current_paragraph = ""
    for line in lines:
        line = line.strip()
        if not line:
            if current_paragraph:
                clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
                if clean_text.strip():
                    story.append(Paragraph(clean_text, styles['Normal']))
                    story.append(Spacer(1, 12))
                current_paragraph = ""
            continue
        if line.startswith('<h'):
            if current_paragraph:
                clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
                if clean_text.strip():
                    story.append(Paragraph(clean_text, styles['Normal']))
                    story.append(Spacer(1, 12))
                current_paragraph = ""
            clean_heading = re.sub(r'<[^>]+>', '', line)
            if clean_heading.strip():
                story.append(Paragraph(clean_heading, heading_style))
                story.append(Spacer(1, 6))
        else:
            current_paragraph += " " + line

    if current_paragraph:
        clean_text = re.sub(r'<[^>]+>', '', current_paragraph)
        if clean_text.strip():
            story.append(Paragraph(clean_text, styles['Normal']))

    try:
        doc.build(story)
        pdf_bytes = buffer.getvalue()
        buffer.close()
        return pdf_bytes
    except Exception as e:
        buffer.close()
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")
