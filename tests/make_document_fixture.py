"""Reproducible, wholly synthetic PDF; run with bundled reportlab/Pillow."""
from io import BytesIO
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont

target = Path(__file__).parent / "fixtures/documents/control.pdf"
target.parent.mkdir(parents=True, exist_ok=True)
pdf = canvas.Canvas(str(target), pagesize=(520, 680), invariant=1)
pdf.setTitle("Synthetic document intake - text, table and image")
pdf.setAuthor("Hermes BNN isolated intake tests")
pdf.setFont("Helvetica-Bold", 19)
pdf.drawString(42, 620, "Synthetic measurement record")
pdf.setFont("Helvetica", 12)
pdf.drawString(42, 587, "Table 1. Two states, no causal identification.")
for y, left, right in [(535, "State", "Value"), (490, "Before", "20"), (445, "After", "30")]:
    pdf.rect(42, y - 14, 436, 38)
    pdf.line(280, y - 14, 280, y + 24)
    pdf.drawString(58, y, left); pdf.drawString(300, y, right)
pdf.drawString(42, 382, "Absolute difference: +10. Relative change: +50%.")
pdf.drawString(42, 350, "Footnote: invented data; units and dates are unspecified.")
pdf.drawString(42, 318, "Do not infer a population rate, significance or causation.")
pdf.setFont("Helvetica", 10); pdf.drawRightString(478, 32, "1 / 2")
pdf.showPage()
pdf.setFont("Helvetica-Bold", 19); pdf.drawString(42, 620, "Visual-only control values")
pdf.setFont("Helvetica", 12)
pdf.drawString(42, 587, "Figure 1. Numbers below exist only in raster pixels.")
im = Image.new("RGB", (850, 550), "white")
draw = ImageDraw.Draw(im)
font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 30)
draw.line((90, 60, 90, 465, 780, 465), fill="#364455", width=4)
draw.rectangle((180, 260, 330, 464), fill="#2F6690")
draw.rectangle((500, 158, 650, 464), fill="#3A897B")
draw.text((224, 215), "20", fill="#243647", font=font)
draw.text((544, 110), "30", fill="#243647", font=font)
draw.text((188, 490), "BEFORE", fill="#243647", font=font)
draw.text((519, 490), "AFTER", fill="#243647", font=font)
data = BytesIO(); im.save(data, format="PNG"); data.seek(0)
pdf.drawImage(ImageReader(data), 42, 225, width=436, height=282)
pdf.drawString(42, 180, "Image delivery is required, even when text is extracted.")
pdf.setFont("Helvetica", 10); pdf.drawRightString(478, 32, "2 / 2")
pdf.save()
print(target)
