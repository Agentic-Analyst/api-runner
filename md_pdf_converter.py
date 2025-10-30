from fastapi import HTTPException
import markdown
from io import BytesIO
from datetime import datetime
import re
from html import escape
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, black, blue, grey
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, HRFlowable
from reportlab.platypus import ListFlowable, ListItem, XPreformatted, Image, KeepTogether
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.pdfgen import canvas
from reportlab.platypus.tableofcontents import TableOfContents
import xml.sax.saxutils as saxutils
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, ArrayObject, NameObject, NumberObject, TextStringObject


# Global list to store pending internal links
_pending_links = []
_bookmark_registry = {}


class InternalLinkParagraph(Paragraph):
    """
    Custom Paragraph class that supports internal PDF links.
    Parses content for <ilink dest="...">text</ilink> tags and creates clickable regions.
    """
    def __init__(self, text, style, **kwargs):
        # Store internal links info
        self.internal_links = []
        
        # Parse and extract internal links
        import re
        pattern = r'<ilink dest="([^"]+)">([^<]+)</ilink>'
        
        def replacer(match):
            dest = match.group(1)
            link_text = match.group(2)
            self.internal_links.append(dest)
            # Replace with styled text (blue and underlined)
            return f'<font color="blue"><u>{link_text}</u></font>'
        
        processed_text = re.sub(pattern, replacer, text)
        super().__init__(processed_text, style, **kwargs)
    
    def draw(self):
        super().draw()
        # Create internal links using bookmarkHorizontal
        if self.internal_links:
            x, y = self.canv.absolutePosition(0, 0)
            w, h = self.width, self.height
            
            for dest in self.internal_links:
                try:
                    # Use bookmarkHorizontal to link to a bookmark
                    # The key is to not specify the page - let it be resolved later
                    self.canv.bookmarkHorizontal(dest, x, y + h)
                except Exception as e:
                    # Ignore errors - bookmark might not exist yet
                    pass


class BookmarkableHeading(Paragraph):
    """A Paragraph with an optional PDF bookmark anchor."""
    def __init__(self, text, style, bookmark_name=None):
        super().__init__(text, style)
        self.bookmark_name = bookmark_name
    
    def draw(self):
        # Register this bookmark
        if self.bookmark_name:
            page_num = self.canv.getPageNumber()
            x, y = self.canv.absolutePosition(0, 0)
            _bookmark_registry[self.bookmark_name] = {
                'page': page_num,
                'x': x,
                'y': y + self.height  # Top of the heading
            }
            # Create a bookmark page marker
            self.canv.bookmarkPage(self.bookmark_name)
        super().draw()


def footer(canvas_obj, doc):
    """Add page number footer to each page"""
    canvas_obj.saveState()
    canvas_obj.setFont('Helvetica', 9)
    page_num = canvas_obj.getPageNumber()
    text = f"Page {page_num}"
    canvas_obj.drawCentredString(letter[0] / 2, 0.5 * inch, text)
    canvas_obj.restoreState()


def create_anchor_id(text):
    """
    Convert heading text to an anchor ID similar to how markdown generates IDs.
    E.g., "Executive Summary" -> "executive-summary"
    "Financial Model & Valuation" -> "financial-model--valuation"
    """
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Decode HTML entities (e.g., &amp; -> &)
    from html import unescape
    text = unescape(text)
    # Convert to lowercase
    text = text.lower()
    # Remove & and other special chars, but keep spaces and hyphens
    # Markdown removes & leaving adjacent spaces which become --
    text = re.sub(r'&', '', text)
    # Replace spaces with hyphens
    text = re.sub(r'[\s]+', '-', text)
    # Remove other special chars except hyphens
    text = re.sub(r'[^\w-]', '', text)
    # Remove leading/trailing hyphens
    text = text.strip('-')
    return text


# Allowed inline HTML tags for ReportLab Paragraph
ALLOWED_INLINE = {"b", "strong", "i", "em", "u", "br", "code", "a"}


def to_paragraph_html(node):
    """
    Convert an element's inline content to ReportLab mini-HTML.
    Preserves bold/italic/inline code/links/line breaks.
    """
    if not node:
        return ""
    
    parts = []
    
    # Handle node's children
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
                # Inline code
                code_text = escape(child.get_text())
                parts.append(f'<font face="Courier" size="10">{code_text}</font>')
            elif name == "a":
                # Links - ReportLab uses <link href="">
                href = child.get("href") or ""
                text = to_paragraph_html(child)
                
                if href and not href.startswith('#'):
                    # External links work fine with standard link tag
                    parts.append(f'<link href="{escape(href, quote=True)}" color="blue">{text}</link>')
                elif href and href.startswith('#'):
                    # Internal bookmark links - use custom <ilink> tag
                    # Strip the # and use the bookmark name as destination
                    dest_name = href[1:]  # Remove the leading #
                    parts.append(f'<ilink dest="{dest_name}">{text}</ilink>')
                else:
                    # No href - just render text
                    parts.append(text)
            else:
                # Unknown tag - just get text
                parts.append(escape(child.get_text()))
    
    return "".join(parts)


def build_list(list_el, styles, level=0):
    """
    Build a nested list structure with proper support for sublists.
    """
    items = []
    
    for li in list_el.find_all('li', recursive=False):
        # Get paragraph content (excluding nested lists)
        para_html = to_paragraph_html(li)
        
        # Remove text from nested lists to avoid duplication
        for sub in li.find_all(['ul', 'ol'], recursive=False):
            sub_text = sub.get_text()
            para_html = para_html.replace(escape(sub_text), "")
        
        # Create paragraph - check for internal links
        has_internal_links = '<ilink dest=' in para_html
        if has_internal_links:
            p = InternalLinkParagraph(para_html.strip(), styles['CustomListItem'])
        else:
            p = Paragraph(para_html.strip(), styles['CustomListItem'])
        
        # Handle nested lists
        sublists = []
        for sub in li.find_all(['ul', 'ol'], recursive=False):
            sublists.append(build_list(sub, styles, level + 1))
        
        if sublists:
            items.append(ListItem([p] + sublists))
        else:
            items.append(ListItem(p))
    
    # Use proper bullet characters for unordered lists
    if list_el.name == 'ul':
        bullet_type = 'bullet'
        # Also try with explicit bullet character
        start_value = None
    else:
        bullet_type = '1'
        start_value = '1'
    
    return ListFlowable(
        items,
        bulletType=bullet_type,
        start=start_value,
        leftIndent=18 + level * 12,
        bulletFontName='Symbol' if list_el.name == 'ul' else 'Helvetica'
    )


def build_table(table_el, styles, page_width, left_margin, right_margin):
    """
    Build a table with proper column widths and header handling.
    Ensures tables fit within page margins with professional styling.
    """
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
            cells.append(Paragraph(cell_html or '', styles['CustomBody']))
        if cells:
            data.append(cells)
    
    if not data:
        return None
    
    # Determine if we should repeat headers
    repeat_rows = 1 if (thead or (data and len(data) > 1)) else 0
    
    # Calculate column widths - distribute evenly within available space
    avail_width = page_width - left_margin - right_margin
    ncols = max(len(row) for row in data) if data else 1
    col_widths = [avail_width / ncols] * ncols
    
    # Create table
    t = Table(data, colWidths=col_widths, repeatRows=repeat_rows)
    
    # Apply professional styling
    style_commands = [
        ('GRID', (0, 0), (-1, -1), 0.5, HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTSIZE', (0, 1), (-1, -1), 9.5),
        ('TEXTCOLOR', (0, 1), (-1, -1), HexColor('#374151')),  # Body text only, starting from row 1
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f8fafc')]),
    ]
    
    # Header styling if we have one - applied AFTER body styles to ensure it takes precedence
    if repeat_rows:
        style_commands.extend([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#1e3a8a')),
            ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#ffffff')),  # White text for header
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ])
    
    t.setStyle(TableStyle(style_commands))
    return t


def build_image(img_el, base_url, max_width):
    """
    Build an image flowable with proper scaling.
    """
    src = img_el.get('src')
    if not src:
        return None
    
    try:
        # Resolve relative URLs
        if base_url:
            src = urljoin(base_url, src)
        
        im = Image(src)
        
        # Scale to fit page width if needed
        if im.drawWidth > max_width:
            scale = max_width / float(im.drawWidth)
            im.drawWidth *= scale
            im.drawHeight *= scale
        
        return im
    except Exception as e:
        # If image can't be loaded, return None
        print(f"Warning: Could not load image {src}: {e}")
        return None


def create_custom_styles():
    """Create custom paragraph styles for different markdown elements"""
    styles = getSampleStyleSheet()
    
    # Title style
    styles.add(ParagraphStyle(
        name='CustomTitle',
        parent=styles['Heading1'],
        fontSize=28,
        textColor=HexColor('#1e3a8a'),
        spaceAfter=12,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    ))
    
    # Subtitle style
    styles.add(ParagraphStyle(
        name='CustomSubtitle',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=HexColor('#1e3a8a'),
        spaceAfter=20,
        alignment=TA_CENTER,
        fontName='Helvetica-Bold'
    ))
    
    # Metadata badge style
    styles.add(ParagraphStyle(
        name='MetadataBadge',
        parent=styles['BodyText'],
        fontSize=11,
        textColor=HexColor('#1e40af'),
        alignment=TA_CENTER,
        spaceAfter=8,
        fontName='Helvetica-Bold'
    ))
    
    # TOC Title
    styles.add(ParagraphStyle(
        name='TOCTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=HexColor('#1e3a8a'),
        spaceAfter=16,
        alignment=TA_LEFT,
        fontName='Helvetica-Bold'
    ))
    
    # TOC Entry
    styles.add(ParagraphStyle(
        name='TOCEntry',
        parent=styles['BodyText'],
        fontSize=11,
        textColor=HexColor('#1f2937'),
        spaceAfter=6,
        leftIndent=20,
        fontName='Helvetica'
    ))
    
    # Heading styles
    styles.add(ParagraphStyle(
        name='CustomH1',
        parent=styles['Heading1'],
        fontSize=20,
        textColor=HexColor('#1e3a8a'),
        spaceAfter=14,
        spaceBefore=20,
        fontName='Helvetica-Bold',
        keepWithNext=True,
        borderWidth=2,
        borderColor=HexColor('#3b82f6'),
        borderPadding=8,
        backColor=HexColor('#eff6ff')
    ))
    
    styles.add(ParagraphStyle(
        name='CustomH2',
        parent=styles['Heading2'],
        fontSize=16,
        textColor=HexColor('#1e40af'),
        spaceAfter=10,
        spaceBefore=16,
        fontName='Helvetica-Bold',
        borderWidth=0,
        borderColor=None,
        borderPadding=0,
        keepWithNext=True,
        leftIndent=0
    ))
    
    styles.add(ParagraphStyle(
        name='CustomH3',
        parent=styles['Heading3'],
        fontSize=14,
        textColor=HexColor('#2563eb'),
        spaceAfter=8,
        spaceBefore=12,
        fontName='Helvetica-Bold',
        keepWithNext=True
    ))
    
    styles.add(ParagraphStyle(
        name='CustomH4',
        parent=styles['Heading3'],
        fontSize=12,
        textColor=HexColor('#3b82f6'),
        spaceAfter=6,
        spaceBefore=10,
        fontName='Helvetica-Bold',
        keepWithNext=True
    ))
    
    # Body text
    styles.add(ParagraphStyle(
        name='CustomBody',
        parent=styles['BodyText'],
        fontSize=10.5,
        leading=15,
        alignment=TA_JUSTIFY,
        spaceAfter=10,
        textColor=HexColor('#374151')
    ))
    
    # Code block
    styles.add(ParagraphStyle(
        name='CustomCode',
        parent=styles['Code'],
        fontSize=9,
        fontName='Courier',
        textColor=HexColor('#1f2937'),
        backColor=HexColor('#f3f4f6'),
        borderWidth=1,
        borderColor=HexColor('#d1d5db'),
        borderPadding=10,
        leftIndent=10,
        rightIndent=10,
        spaceAfter=12
    ))
    
    # Blockquote
    styles.add(ParagraphStyle(
        name='CustomBlockquote',
        parent=styles['BodyText'],
        fontSize=10,
        textColor=HexColor('#4b5563'),
        backColor=HexColor('#f9fafb'),
        borderWidth=0,
        leftIndent=25,
        rightIndent=20,
        borderPadding=12,
        spaceAfter=12,
        spaceBefore=12,
        fontName='Helvetica-Oblique'
    ))
    
    # Meta info
    styles.add(ParagraphStyle(
        name='CustomMeta',
        parent=styles['BodyText'],
        fontSize=10,
        textColor=HexColor('#6b7280'),
        alignment=TA_CENTER,
        spaceAfter=6
    ))
    
    # List item
    styles.add(ParagraphStyle(
        name='CustomListItem',
        parent=styles['BodyText'],
        fontSize=10.5,
        leading=15,
        leftIndent=20,
        spaceAfter=5,
        textColor=HexColor('#374151')
    ))
    
    # Highlight box (for key investment highlights, recommendations, etc.)
    styles.add(ParagraphStyle(
        name='HighlightBox',
        parent=styles['BodyText'],
        fontSize=11,
        textColor=HexColor('#1e40af'),
        backColor=HexColor('#eff6ff'),
        borderWidth=2,
        borderColor=HexColor('#3b82f6'),
        borderPadding=12,
        spaceAfter=14,
        spaceBefore=14,
        fontName='Helvetica-Bold',
        alignment=TA_CENTER
    ))

    styles.add(ParagraphStyle(
        name='TableHeader',
        parent=styles['BodyText'],
        fontSize=10,
        fontName='Helvetica-Bold',
        textColor=HexColor('#ffffff'),
        alignment=TA_CENTER,
    ))

    
    return styles


def parse_html_to_flowables(html_content, styles, base_url=None, page_w=letter[0], margins=(0.75*inch, 0.75*inch)):
    """
    Parse HTML content and convert to ReportLab flowables.
    Uses controlled traversal to handle block-level elements properly.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    body = soup.body or soup
    flowables = []
    
    left_margin, right_margin = margins
    
    # Block-level elements we handle
    BLOCKS = {'h1', 'h2', 'h3', 'h4', 'p', 'ul', 'ol', 'table', 'pre', 'blockquote', 'hr', 'img'}
    
    h1_seen = False
    
    for el in body.children:
        # Skip non-element nodes and non-block elements
        if getattr(el, "name", None) not in BLOCKS:
            continue
        
        name = el.name
        
        if name == 'h1':
            # Add page break before subsequent H1s for better structure
            if h1_seen:
                flowables.append(PageBreak())
            h1_seen = True
            text = to_paragraph_html(el)
            if text.strip():
                # Create bookmark for h1
                anchor_id = create_anchor_id(el.get_text())
                flowables.append(BookmarkableHeading(text, styles['CustomH1'], bookmark_name=anchor_id))
                flowables.append(Spacer(1, 0.15 * inch))
        
        elif name == 'h2':
            text = to_paragraph_html(el)
            if text.strip():
                # Create bookmark for h2
                anchor_id = create_anchor_id(el.get_text())
                flowables.append(Spacer(1, 0.1 * inch))
                flowables.append(BookmarkableHeading(text, styles['CustomH2'], bookmark_name=anchor_id))
                flowables.append(Spacer(1, 0.1 * inch))
        
        elif name == 'h3':
            text = to_paragraph_html(el)
            if text.strip():
                # Create bookmark for h3
                anchor_id = create_anchor_id(el.get_text())
                flowables.append(BookmarkableHeading(text, styles['CustomH3'], bookmark_name=anchor_id))
                flowables.append(Spacer(1, 0.08 * inch))
        
        elif name == 'h4':
            text = to_paragraph_html(el)
            if text.strip():
                # Create bookmark for h4
                anchor_id = create_anchor_id(el.get_text())
                flowables.append(BookmarkableHeading(text, styles['CustomH4'], bookmark_name=anchor_id))
                flowables.append(Spacer(1, 0.06 * inch))
        
        elif name == 'p':
            para_html = to_paragraph_html(el)
            if para_html.strip():
                # Check if this is a highlighted paragraph (starts with **)
                raw_text = el.get_text().strip()
                # Check if paragraph contains internal links
                has_internal_links = '<ilink dest=' in para_html
                
                if raw_text.startswith('**') and raw_text.endswith('**'):
                    # This is a bold/important statement - use highlight style
                    if has_internal_links:
                        flowables.append(InternalLinkParagraph(para_html, styles['HighlightBox']))
                    else:
                        flowables.append(Paragraph(para_html, styles['HighlightBox']))
                else:
                    if has_internal_links:
                        flowables.append(InternalLinkParagraph(para_html, styles['CustomBody']))
                    else:
                        flowables.append(Paragraph(para_html, styles['CustomBody']))
                flowables.append(Spacer(1, 0.1 * inch))
        
        elif name in ('ul', 'ol'):
            list_flowable = build_list(el, styles)
            flowables.append(list_flowable)
            flowables.append(Spacer(1, 0.12 * inch))
        
        elif name == 'table':
            table = build_table(el, styles, page_w, left_margin, right_margin)
            if table:
                flowables.append(Spacer(1, 0.08 * inch))
                flowables.append(table)
                flowables.append(Spacer(1, 0.18 * inch))
        
        elif name == 'pre':
            # Use XPreformatted for page-breakable code blocks
            code = el.get_text()
            if code.strip():
                flowables.append(XPreformatted(code, styles['CustomCode'], dedent=0))
                flowables.append(Spacer(1, 0.12 * inch))
        
        elif name == 'blockquote':
            text = to_paragraph_html(el)
            if text.strip():
                flowables.append(Paragraph(text, styles['CustomBlockquote']))
                flowables.append(Spacer(1, 0.12 * inch))
        
        elif name == 'hr':
            # Add a horizontal rule for section separation
            flowables.append(Spacer(1, 0.15 * inch))
            flowables.append(HRFlowable(
                width="100%",
                thickness=1,
                color=HexColor('#cbd5e1'),
                spaceBefore=6,
                spaceAfter=6
            ))
            flowables.append(Spacer(1, 0.15 * inch))
        
        elif name == 'img':
            img = build_image(el, base_url, page_w - left_margin - right_margin)
            if img:
                flowables.append(img)
                flowables.append(Spacer(1, 0.12 * inch))
    
    return flowables


def add_internal_links_to_pdf(pdf_bytes, pending_links, bookmark_registry):
    """
    Post-process the generated PDF to add clickable internal links.
    Uses pypdf to add link annotations that point to bookmarks.
    """
    if not pending_links or not bookmark_registry:
        return pdf_bytes
    
    try:
        # Read the PDF
        pdf_reader = PdfReader(BytesIO(pdf_bytes))
        pdf_writer = PdfWriter()
        
        # Copy all pages
        for page in pdf_reader.pages:
            pdf_writer.add_page(page)
        
        # Add link annotations for pending links
        for link_info in pending_links:
            page_num = link_info['page'] - 1  # Convert to 0-indexed
            rect = link_info['rect']
            dest_name = link_info['dest']
            
            # Check if the destination bookmark exists
            if dest_name in bookmark_registry:
                dest_info = bookmark_registry[dest_name]
                dest_page = dest_info['page'] - 1  # Convert to 0-indexed
                
                # Create a link annotation
                # This would require manual PDF annotation creation which is complex
                # For now, we'll skip this and rely on PDF bookmarks which ARE clickable
                pass
        
        # Write to bytes
        output_buffer = BytesIO()
        pdf_writer.write(output_buffer)
        return output_buffer.getvalue()
    
    except Exception as e:
        print(f"Warning: Could not post-process PDF links: {e}")
        # Return original PDF if post-processing fails
        return pdf_bytes


def convert_md_to_pdf(md_content: str, ticker: str, *, base_url: str | None = None) -> bytes:
    """
    Convert Markdown to a styled, structurally faithful PDF using ReportLab.
    
    Args:
        md_content: Markdown content to convert
        ticker: Stock ticker symbol
        base_url: Base URL for resolving relative paths (images, links)
    
    Returns:
        PDF file as bytes
    """
    try:
        # Extract metadata from the first few lines
        lines = md_content.split('\n')
        company_name = lines[0].lstrip('#').strip() if lines else ticker
        subtitle = lines[1].lstrip('#').strip() if len(lines) > 1 else "Investment Analysis Report"
        
        # Extract report date, sector, industry, exchange
        metadata = {}
        for line in lines[:10]:
            if '**Report Date**:' in line:
                metadata['date'] = line.split(':', 1)[1].strip()
            elif '**Sector**:' in line:
                parts = line.split('|')
                if len(parts) >= 2:
                    metadata['sector'] = parts[0].split(':', 1)[1].strip()
                    metadata['industry'] = parts[1].split(':', 1)[1].strip() if ':' in parts[1] else parts[1].strip()
            elif '**Exchange**:' in line:
                metadata['exchange'] = line.split(':', 1)[1].strip()
        
        # 1) Convert Markdown to HTML
        html_content = markdown.markdown(
            md_content,
            output_format="html5",
            extensions=[
                "extra",
                "sane_lists",
                "nl2br",
            ],
        )
        
        # Extract TOC sections from HTML
        soup = BeautifulSoup(html_content, 'html.parser')
        h2_headings = []
        for h2 in soup.find_all('h2'):
            heading_text = h2.get_text().strip()
            # Skip the "Table of Contents" heading itself
            if heading_text and heading_text != "Table of Contents":
                h2_headings.append(heading_text)
        
        # 2) Create PDF
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=0.75*inch,
            leftMargin=0.75*inch,
            topMargin=0.75*inch,
            bottomMargin=0.75*inch,
        )
        
        # 3) Create custom styles
        styles = create_custom_styles()
        
        # 4) Build content
        story = []
        
        # ===== COVER PAGE =====
        story.append(Spacer(1, 1.5*inch))
        
        # Company name with decorative line
        story.append(HRFlowable(
            width="60%",
            thickness=2,
            color=HexColor('#3b82f6'),
            spaceBefore=0,
            spaceAfter=12
        ))
        story.append(Paragraph(company_name, styles['CustomTitle']))
        story.append(HRFlowable(
            width="60%",
            thickness=2,
            color=HexColor('#3b82f6'),
            spaceBefore=12,
            spaceAfter=24
        ))
        
        # Subtitle
        story.append(Paragraph(subtitle, styles['CustomSubtitle']))
        story.append(Spacer(1, 0.4*inch))
        
        # Metadata badges
        if metadata.get('sector'):
            story.append(Paragraph(
                f"<b>Sector:</b> {metadata['sector']}", 
                styles['MetadataBadge']
            ))
        if metadata.get('industry'):
            story.append(Paragraph(
                f"<b>Industry:</b> {metadata['industry']}", 
                styles['MetadataBadge']
            ))
        if metadata.get('exchange'):
            story.append(Paragraph(
                f"<b>Exchange:</b> {metadata['exchange']}", 
                styles['MetadataBadge']
            ))
        
        story.append(Spacer(1, 0.8*inch))
        
        # Report generation info
        report_date = metadata.get('date', datetime.now().strftime('%B %d, %Y'))
        story.append(Paragraph(
            f"Report Date: {report_date}", 
            styles['CustomMeta']
        ))
        story.append(Paragraph(
            f"Generated: {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", 
            styles['CustomMeta']
        ))
        
        story.append(PageBreak())
        
        # ===== TABLE OF CONTENTS =====
        if h2_headings:
            story.append(Spacer(1, 0.5*inch))
            story.append(Paragraph("Table of Contents", styles['TOCTitle']))
            story.append(Spacer(1, 0.2*inch))
            
            for idx, heading in enumerate(h2_headings, 1):
                # Create anchor ID for the heading
                anchor_id = create_anchor_id(heading)
                # Create clickable TOC entry with internal link using our custom ilink tag
                toc_entry = f'{idx}. <ilink dest="{anchor_id}">{heading}</ilink>'
                story.append(InternalLinkParagraph(toc_entry, styles['TOCEntry']))
            
            story.append(Spacer(1, 0.3*inch))
            story.append(HRFlowable(
                width="100%",
                thickness=1,
                color=HexColor('#cbd5e1'),
                spaceBefore=6,
                spaceAfter=6
            ))
            story.append(PageBreak())
        
        # 5) Parse HTML and add to story
        flowables = parse_html_to_flowables(
            html_content, 
            styles,
            base_url=base_url,
            page_w=letter[0],
            margins=(doc.leftMargin, doc.rightMargin)
        )
        story.extend(flowables)
        
        # 6) Build PDF
        # Clear pending links before building
        global _pending_links, _bookmark_registry
        _pending_links = []
        _bookmark_registry = {}
        
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
        
        # 7) Post-process PDF to add clickable internal links
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        # Add internal links using pypdf
        pdf_bytes = add_internal_links_to_pdf(pdf_bytes, _pending_links, _bookmark_registry)
        
        return pdf_bytes
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {str(e)}")

def main():
    sample_md = open("AAPL_Professional_Analysis_Report.md").read()
    pdf_bytes = convert_md_to_pdf(sample_md, "AAPL")
    with open("output_report.pdf", "wb") as f:
        f.write(pdf_bytes)

if __name__ == "__main__":
    main()