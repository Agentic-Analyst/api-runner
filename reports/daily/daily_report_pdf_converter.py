"""
Lightweight PDF Converter for Daily Intelligence Reports
Optimized for Company and Sector daily news reports
"""

from fastapi import HTTPException
import markdown
from io import BytesIO
from datetime import datetime
import re
from html import escape
from bs4 import BeautifulSoup
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
from reportlab.platypus import ListFlowable, ListItem, KeepTogether
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfgen import canvas


def footer(canvas_obj, doc):
    """Add page number footer to each page"""
    canvas_obj.saveState()
    canvas_obj.setFont('Helvetica', 8)
    page_num = canvas_obj.getPageNumber()
    text = f"Page {page_num}"
    canvas_obj.drawCentredString(letter[0] / 2, 0.5 * inch, text)
    canvas_obj.restoreState()


def create_daily_report_styles():
    """Create custom paragraph styles optimized for daily reports"""
    styles = getSampleStyleSheet()
    
    # Main title style (with emoji support)
    styles.add(ParagraphStyle(
        name='ReportTitle',
        parent=styles['Heading1'],
        fontSize=22,
        textColor=HexColor('#1a1a1a'),
        spaceAfter=12,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    ))
    
    # Metadata style
    styles.add(ParagraphStyle(
        name='Metadata',
        parent=styles['BodyText'],
        fontSize=10,
        textColor=HexColor('#4a5568'),
        alignment=TA_CENTER,
        spaceAfter=4,
        fontName='Helvetica'
    ))
    
    # Section headers (0️⃣, 1️⃣, etc.)
    styles.add(ParagraphStyle(
        name='SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        textColor=HexColor('#1e40af'),
        spaceAfter=10,
        spaceBefore=16,
        fontName='Helvetica-Bold',
        backColor=HexColor('#eff6ff'),
        borderPadding=10,
        borderWidth=1,
        borderColor=HexColor('#3b82f6'),
        leftIndent=0
    ))
    
    # Subsection headers
    styles.add(ParagraphStyle(
        name='SubsectionHeader',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=HexColor('#1e40af'),
        spaceAfter=8,
        spaceBefore=10,
        fontName='Helvetica-Bold'
    ))
    
    # Body text
    styles.add(ParagraphStyle(
        name='ReportBody',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
        textColor=HexColor('#1f2937')
    ))
    
    # Blockquote/Insight box
    styles.add(ParagraphStyle(
        name='InsightBox',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        textColor=HexColor('#065f46'),
        backColor=HexColor('#d1fae5'),
        borderWidth=1,
        borderColor=HexColor('#10b981'),
        borderPadding=12,
        leftIndent=15,
        rightIndent=15,
        spaceAfter=14,
        spaceBefore=10,
        fontName='Helvetica'
    ))
    
    # Context/Note box
    styles.add(ParagraphStyle(
        name='ContextBox',
        parent=styles['BodyText'],
        fontSize=9.5,
        leading=13,
        textColor=HexColor('#374151'),
        backColor=HexColor('#f3f4f6'),
        borderWidth=1,
        borderColor=HexColor('#d1d5db'),
        borderPadding=10,
        spaceAfter=12,
        spaceBefore=8
    ))
    
    # List item
    styles.add(ParagraphStyle(
        name='ReportListItem',
        parent=styles['BodyText'],
        fontSize=10,
        leading=14,
        leftIndent=20,
        spaceAfter=6,
        textColor=HexColor('#374151')
    ))
    
    # Table header
    styles.add(ParagraphStyle(
        name='TableHeader',
        parent=styles['BodyText'],
        fontSize=9,
        fontName='Helvetica-Bold',
        textColor=HexColor('#ffffff'),
        alignment=TA_CENTER,
    ))
    
    # Table cell
    styles.add(ParagraphStyle(
        name='TableCell',
        parent=styles['BodyText'],
        fontSize=9,
        textColor=HexColor('#374151'),
    ))
    
    # Footer note
    styles.add(ParagraphStyle(
        name='FooterNote',
        parent=styles['BodyText'],
        fontSize=9,
        textColor=HexColor('#6b7280'),
        alignment=TA_CENTER,
        spaceAfter=8
    ))
    
    return styles


def to_paragraph_html(node):
    """Convert an element's inline content to ReportLab mini-HTML"""
    if not node:
        return ""
    
    parts = []
    
    if hasattr(node, 'children'):
        for child in node.children:
            if getattr(child, "name", None) is None:
                # Text node
                parts.append(escape(str(child)))
                continue
            
            name = child.name.lower()
            
            if name in ("b", "strong"):
                parts.append(f"<b>{to_paragraph_html(child)}</b>")
            elif name in ("i", "em"):
                parts.append(f"<i>{to_paragraph_html(child)}</i>")
            elif name == "u":
                parts.append(f"<u>{to_paragraph_html(child)}</u>")
            elif name == "br":
                parts.append("<br/>")
            elif name == "code":
                code_text = escape(child.get_text())
                parts.append(f'<font face="Courier" size="9" color="#c7254e" backColor="#f9f2f4">{code_text}</font>')
            elif name == "a":
                href = child.get("href") or ""
                text = to_paragraph_html(child)
                if href and not href.startswith('#'):
                    parts.append(f'<link href="{escape(href, quote=True)}" color="blue">{text}</link>')
                else:
                    parts.append(text)
            else:
                parts.append(escape(child.get_text()))
    
    return "".join(parts)


def build_list(list_el, styles):
    """Build a list structure"""
    items = []
    
    for li in list_el.find_all('li', recursive=False):
        para_html = to_paragraph_html(li)
        for sub in li.find_all(['ul', 'ol'], recursive=False):
            sub_text = sub.get_text()
            para_html = para_html.replace(escape(sub_text), "")
        
        p = Paragraph(para_html.strip(), styles['ReportListItem'])
        items.append(ListItem(p))
    
    bullet_type = 'bullet' if list_el.name == 'ul' else '1'
    return ListFlowable(
        items,
        bulletType=bullet_type,
        start='1' if list_el.name == 'ol' else None,
        leftIndent=18,
        bulletFontName='Symbol' if list_el.name == 'ul' else 'Helvetica'
    )


def build_table(table_el, styles, page_width, left_margin, right_margin):
    """Build a professional table with smart column sizing"""
    data = []
    thead = table_el.find('thead')
    tbody = table_el.find('tbody')
    
    # Process header
    if thead:
        head_cells = []
        for th in thead.find_all('th'):
            cell_html = to_paragraph_html(th)
            head_cells.append(Paragraph(cell_html or '', styles['TableHeader']))
        if head_cells:
            data.append(head_cells)
    
    # Process body rows
    body_parent = tbody or table_el
    for tr in body_parent.find_all('tr', recursive=False):
        cells = []
        for td in tr.find_all(['td', 'th'], recursive=False):
            cell_html = to_paragraph_html(td)
            cells.append(Paragraph(cell_html or '', styles['TableCell']))
        if cells:
            data.append(cells)
    
    if not data:
        return None
    
    # Smart column width allocation
    avail_width = page_width - left_margin - right_margin
    ncols = max(len(row) for row in data) if data else 1
    
    # Allocate more space to text-heavy columns
    col_widths = [avail_width / ncols] * ncols
    
    # Create table
    t = Table(data, colWidths=col_widths, repeatRows=1 if thead else 0)
    
    # Modern table styling
    style_commands = [
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#e5e7eb')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('TEXTCOLOR', (0, 1), (-1, -1), HexColor('#374151')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f9fafb')]),
    ]
    
    # Header styling
    if thead or (data and len(data) > 1):
        style_commands.extend([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#3b82f6')),
            ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ffffff')),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ])
    
    t.setStyle(TableStyle(style_commands))
    return t


def parse_html_to_flowables(html_content, styles, page_w=letter[0], margins=(0.75*inch, 0.75*inch)):
    """Parse HTML content and convert to ReportLab flowables"""
    soup = BeautifulSoup(html_content, 'html.parser')
    body = soup.body or soup
    flowables = []
    
    left_margin, right_margin = margins
    BLOCKS = {'h1', 'h2', 'h3', 'h4', 'p', 'ul', 'ol', 'table', 'pre', 'blockquote', 'hr'}
    
    # Counter for H2 sections
    section_counter = 0
    
    for el in body.children:
        if getattr(el, "name", None) not in BLOCKS:
            continue
        
        name = el.name
        
        if name == 'h1':
            text = to_paragraph_html(el)
            if text.strip():
                flowables.append(Paragraph(text, styles['ReportTitle']))
                flowables.append(Spacer(1, 0.15 * inch))
        
        elif name == 'h2':
            text = to_paragraph_html(el)
            if text.strip():
                section_counter += 1
                # Simple section number
                formatted_text = f'<b>{section_counter}.</b> {text}'
                flowables.append(Spacer(1, 0.12 * inch))
                flowables.append(Paragraph(formatted_text, styles['SectionHeader']))
                flowables.append(Spacer(1, 0.08 * inch))
        
        elif name == 'h3':
            text = to_paragraph_html(el)
            if text.strip():
                flowables.append(Paragraph(text, styles['SubsectionHeader']))
                flowables.append(Spacer(1, 0.06 * inch))
        
        elif name == 'p':
            para_html = to_paragraph_html(el)
            if para_html.strip():
                # Check for **Context**: or **TL;DR:** patterns
                raw_text = el.get_text().strip()
                if raw_text.startswith('**Context**') or raw_text.startswith('**TL;DR:**') or raw_text.startswith('**Insights**'):
                    flowables.append(Paragraph(para_html, styles['ContextBox']))
                else:
                    flowables.append(Paragraph(para_html, styles['ReportBody']))
                flowables.append(Spacer(1, 0.08 * inch))
        
        elif name in ('ul', 'ol'):
            list_flowable = build_list(el, styles)
            flowables.append(list_flowable)
            flowables.append(Spacer(1, 0.10 * inch))
        
        elif name == 'table':
            table = build_table(el, styles, page_w, left_margin, right_margin)
            if table:
                flowables.append(Spacer(1, 0.08 * inch))
                flowables.append(table)
                flowables.append(Spacer(1, 0.16 * inch))
        
        elif name == 'blockquote':
            text = to_paragraph_html(el)
            if text.strip():
                flowables.append(Paragraph(text, styles['InsightBox']))
                flowables.append(Spacer(1, 0.10 * inch))
        
        elif name == 'hr':
            flowables.append(HRFlowable(
                width="100%",
                thickness=1,
                color=HexColor('#d1d5db'),
                spaceBefore=8,
                spaceAfter=8
            ))
    
    return flowables


def convert_daily_report_to_pdf(md_content: str, report_type: str = "Company") -> bytes:
    """
    Convert Daily Intelligence Report Markdown to PDF
    
    Args:
        md_content: Markdown content to convert
        report_type: "Company" or "Sector"
    
    Returns:
        PDF file as bytes
    """
    try:
        # Extract metadata from markdown
        lines = md_content.split('\n')
        title = lines[0].lstrip('#').strip() if lines else "Daily Report"
        
        # Extract company/sector info
        metadata = {}
        for line in lines[:15]:
            if '**Company:**' in line or '**Coverage Universe:**' in line:
                metadata['entity'] = line.split(':', 1)[1].strip()
            elif '**Sector:**' in line:
                metadata['sector'] = line.split(':', 1)[1].strip()
            elif '**Date:**' in line:
                metadata['date'] = line.split(':', 1)[1].strip()
            elif '**Analyst Reading Time:**' in line:
                metadata['read_time'] = line.split(':', 1)[1].strip()
        
        # Convert Markdown to HTML
        html_content = markdown.markdown(
            md_content,
            output_format="html5",
            extensions=[
                "extra",
                "tables",
                "sane_lists",
                "nl2br",
            ],
        )
        
        # Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )
        
        # Create styles
        styles = create_daily_report_styles()
        
        # Build content
        story = []
        
        # === HEADER ===
        story.append(Spacer(1, 0.3*inch))
        
        # Title
        story.append(Paragraph(title, styles['ReportTitle']))
        story.append(Spacer(1, 0.15*inch))
        
        # Metadata
        if metadata.get('entity'):
            story.append(Paragraph(f"<b>{metadata['entity']}</b>", styles['Metadata']))
        if metadata.get('sector'):
            story.append(Paragraph(f"Sector: {metadata['sector']}", styles['Metadata']))
        if metadata.get('date'):
            story.append(Paragraph(f"Date: {metadata['date']}", styles['Metadata']))
        if metadata.get('read_time'):
            story.append(Paragraph(f"Analyst Reading Time: {metadata['read_time']}", styles['Metadata']))
        
        story.append(Spacer(1, 0.2*inch))
        story.append(HRFlowable(
            width="100%",
            thickness=2,
            color=HexColor('#3b82f6'),
            spaceBefore=0,
            spaceAfter=12
        ))
        
        # Parse HTML and add to story
        flowables = parse_html_to_flowables(
            html_content,
            styles,
            page_w=letter[0],
            margins=(doc.leftMargin, doc.rightMargin)
        )
        story.extend(flowables)
        
        # Footer
        story.append(Spacer(1, 0.3*inch))
        story.append(HRFlowable(
            width="100%",
            thickness=1,
            color=HexColor('#d1d5db'),
            spaceBefore=6,
            spaceAfter=6
        ))
        story.append(Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}",
            styles['FooterNote']
        ))
        
        # Build PDF
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        
        # Return PDF bytes
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        return pdf_bytes
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

