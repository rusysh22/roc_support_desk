import io
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

# Create dummy PDF
buf = io.BytesIO()
c = canvas.Canvas(buf)
c.drawString(100, 100, "Hello World")
c.save()
buf.seek(0)

reader = PdfReader(buf)
writer = PdfWriter()

for page in reader.pages:
    writer.add_page(page)

# Try compressing on writer.pages
try:
    for page in writer.pages:
        page.compress_content_streams()
    print("writer.pages: Success")
except Exception as e:
    print("writer.pages: Failed -", e)
