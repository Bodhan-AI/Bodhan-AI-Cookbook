"""Generate a synthetic two-page scheme notice so the recipe runs without any real document.

    python make_sample_notice.py            # writes sample_data/sample_scheme_notice.pdf and .png

The scheme, department, numbers and links are invented and marked as a sample on every page.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
OUT = HERE / "sample_data"

CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.35; }
h1 { font-size: 15pt; text-align: center; margin: 0 0 4pt 0; }
h2 { font-size: 11.5pt; margin: 10pt 0 3pt 0; }
p.dept { text-align: center; font-size: 10pt; margin: 0; }
p.ref { font-size: 9pt; margin: 6pt 0; }
p.mark { font-size: 8pt; text-align: center; color: #888; }
table { border-collapse: collapse; width: 100%; }
td, th { border: 1px solid #444; padding: 3pt 5pt; font-size: 10pt; }
li { margin-bottom: 2pt; }
"""

PAGE_1 = """
<p class="mark">SYNTHETIC SAMPLE FOR THE BODHAN AI COOKBOOK. NOT A REAL GOVERNMENT SCHEME.</p>
<p class="dept">Government of Sampleland<br>Department of Agriculture and Farmers Welfare</p>
<h1>Gramin Solar Pump Sahayata Yojana 2026</h1>
<p class="ref">Notification No. AGRI/SP/2026/114 &nbsp;&nbsp; Dated: 02 November 2026</p>

<h2>Objective</h2>
<p>The scheme helps small and marginal farmers replace diesel irrigation pumps with solar pumps,
reducing their fuel costs and providing reliable daytime irrigation.</p>

<h2>Benefits</h2>
<ul>
<li>A subsidy of 60% of the cost of a solar pump, paid directly into the farmer's bank account.</li>
<li>Women farmers and SC/ST farmers receive an additional 10% subsidy.</li>
<li>Free maintenance of the pump for 5 years by the empanelled vendor.</li>
</ul>
<table>
<tr><th>Pump capacity</th><th>Approved cost</th><th>Maximum subsidy</th></tr>
<tr><td>3 HP</td><td>Rs. 1,50,000</td><td>Rs. 90,000</td></tr>
<tr><td>5 HP</td><td>Rs. 2,20,000</td><td>Rs. 1,32,000</td></tr>
<tr><td>7.5 HP</td><td>Rs. 3,00,000</td><td>Rs. 1,80,000</td></tr>
</table>

<h2>Eligibility</h2>
<ul>
<li>The applicant must be a farmer resident of Sampleland and aged 18 years or above.</li>
<li>The applicant must own or lease agricultural land of up to 5 acres.</li>
<li>Tenant farmers may apply with a registered lease agreement of at least 3 years.</li>
<li>Annual family income must not exceed Rs. 3,00,000.</li>
</ul>

<h2>Who is not eligible</h2>
<ul>
<li>Farmers who received a solar pump subsidy under any government scheme in the last 7 years.</li>
<li>Income tax payers and serving or retired government employees.</li>
</ul>
"""

PAGE_2 = """
<p class="mark">SYNTHETIC SAMPLE FOR THE BODHAN AI COOKBOOK. NOT A REAL GOVERNMENT SCHEME.</p>
<h2>Documents required</h2>
<ul>
<li>Aadhaar card of the applicant.</li>
<li>Land record (khasra/khatauni) or registered lease agreement.</li>
<li>Bank passbook first page with IFSC code.</li>
<li>Income certificate issued in the last 12 months.</li>
<li>Caste certificate, if applying for the additional SC/ST subsidy.</li>
<li>One passport-size photograph.</li>
</ul>

<h2>How to apply</h2>
<ol>
<li>Visit the nearest Common Service Centre (CSC) or apply online at www.example.org/solar-pump.</li>
<li>Fill the application form and upload the documents listed above.</li>
<li>Pay the farmer's share of 40% only after the application is approved and a vendor is assigned.</li>
<li>The vendor installs the pump within 60 days and the subsidy is released after inspection.</li>
</ol>

<h2>Important dates</h2>
<ul>
<li>Applications open: 15 November 2026.</li>
<li>Last date to apply: 31 December 2026.</li>
<li>List of selected farmers published: 31 January 2027.</li>
</ul>

<h2>Helpline</h2>
<p>Toll free helpline 1800-000-1234 (10 am to 6 pm, Monday to Saturday).
Email: solarpump-help@example.org. Grievances can also be raised at the District Agriculture Office.</p>

<p class="ref">By order,<br>Director of Agriculture, Government of Sampleland</p>
"""


def main() -> None:
    OUT.mkdir(exist_ok=True)
    doc = pymupdf.open()
    for html in (PAGE_1, PAGE_2):
        page = doc.new_page(width=595, height=842)  # A4 in points
        page.insert_htmlbox(pymupdf.Rect(50, 40, 545, 810), html, css=CSS)
    pdf = OUT / "sample_scheme_notice.pdf"
    doc.save(pdf)
    # A single-page photo-style PNG of page 1, to show image input too.
    png = OUT / "sample_scheme_notice_page1.png"
    doc[0].get_pixmap(dpi=120).save(png)
    doc.close()
    print(f"wrote {pdf.relative_to(HERE)} ({pdf.stat().st_size // 1024} KB) and {png.relative_to(HERE)}")


if __name__ == "__main__":
    main()
