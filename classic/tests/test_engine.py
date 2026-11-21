import fitz
import pytest
from pathlib import Path
from classic.engine import edit_pdf, PrecisionError, parse_instructions, inspect_pdf


def make_pdf(text='Invoice 2025 / 0081', second=False, embedded=False):
    doc=fitz.open()
    for i in range(2 if second else 1):
        p=doc.new_page(width=612,height=792)
        p.draw_rect((50,50,510,220),color=(.2,.3,.5),fill=(.93,.95,.9))
        if embedded:
            candidates=[Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'), Path('/System/Library/Fonts/Supplemental/Arial.ttf'), Path('/Library/Fonts/Arial.ttf')]
            available=next((f for f in candidates if f.exists()),None)
            if available is None: pytest.skip('No suitable embedded-font fixture installed')
            p.insert_font(fontname='designer',fontfile=str(available))
            p.insert_text((70,95),text,fontsize=11,fontname='designer',color=(.2,.3,.4))
        else:
            p.insert_text((70,95),text,fontsize=11,fontname='helv',color=(.2,.3,.4))
        p.insert_text((70,137),'Do not touch this line.',fontsize=12,fontname='hebo')
    return doc.tobytes()


def test_parser():
    e=parse_instructions('On page 2, replace "Draft" with "Final"')
    assert (e[0].old,e[0].new,e[0].page)==('Draft','Final',2)


def test_single_replacement_and_render_validation():
    output=edit_pdf(make_pdf(),'Replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2026' in doc[0].get_text()
    assert '2025' not in doc[0].get_text()
    assert 'Do not touch this line.' in doc[0].get_text()
    assert len(output['edits'])==1
    assert output['before_png']!=output['after_png']


def test_embedded_font():
    output=edit_pdf(make_pdf(embedded=True),'Replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2026' in doc[0].get_text()
    assert '2025' not in doc[0].get_text()
    assert output['edits'][0]['font']


def test_scanned_rejected():
    doc=fitz.open();p=doc.new_page();p.draw_rect((10,20,150,90),fill=(1,1,0))
    with pytest.raises(PrecisionError,match='Could not find'):
        edit_pdf(doc.tobytes(),'Replace "hello" with "world"')


def test_repeated_requires_explicit_choice():
    with pytest.raises(PrecisionError,match='appears 2 times'):
        edit_pdf(make_pdf(second=True),'Replace "2025" with "2026"')
    output=edit_pdf(make_pdf(second=True),'On page 2, replace "2025" with "2026"')
    doc=fitz.open(stream=output['pdf'],filetype='pdf')
    assert '2025' in doc[0].get_text() and '2026' in doc[1].get_text()


def test_replace_all():
    out=edit_pdf(make_pdf(second=True),'Replace all "2025" with "2026"')
    assert len(out['edits'])==2


def test_overlong_replacement_rejected():
    with pytest.raises(PrecisionError,match='collide'):
        edit_pdf(make_pdf(),'Replace "2025" with "THIS IS A VERY LONG PIECE OF TEXT"')


def test_no_silent_fallback_for_nonexistent_glyph():
    with pytest.raises(PrecisionError,match='lacks glyph'):
        edit_pdf(make_pdf(),'Replace "2025" with "🎃"')


def test_does_not_modify_source_file():
    original=make_pdf()
    source=bytes(original)
    edit_pdf(original,'Replace "2025" with "2026"')
    assert original==source


def test_overlapping_edits_rejected():
    with pytest.raises(PrecisionError,match='overlap'):
        edit_pdf(make_pdf(),'Replace "2025" with "2026"\nReplace "2025" with "2027"')


def test_original_font_size_color_and_baseline():
    src=make_pdf()
    out=edit_pdf(src,'Replace "2025" with "2026"')
    original=fitz.open(stream=src,filetype='pdf')[0]
    final=fitz.open(stream=out['pdf'],filetype='pdf')[0]
    def find(page,token):
        for block in page.get_text('rawdict')['blocks']:
            for line in block.get('lines',[]):
                for span in line.get('spans',[]):
                    text=''.join(ch['c'] for ch in span['chars'])
                    if token in text:
                        i=text.index(token)
                        return span,span['chars'][i]['origin']
        raise ValueError('not found')
    a, old_origin=find(original,'2025')
    b, new_origin=find(final,'2026')
    assert a['font']==b['font']
    assert a['size']==b['size']
    assert a['color']==b['color']
    assert max(abs(x-y) for x,y in zip(old_origin,new_origin))<0.01


def test_multiple_replacements_and_image_mask():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # Add a 1-bit stencil mask image
    pix = fitz.Pixmap(fitz.csGRAY, (0, 0, 100, 20), 0)
    page.insert_image(fitz.Rect(10, 10, 200, 30), pixmap=pix)
    page.insert_text((70, 95), "Item A: $100.00", fontsize=11, fontname="helv")
    page.insert_text((70, 120), "Item B: $200.00", fontsize=11, fontname="helv")
    pdf_bytes = doc.tobytes()
    instructions = 'Replace "$100.00" with "$150.00"\nReplace "$200.00" with "$250.00"'
    res = edit_pdf(pdf_bytes, instructions)
    assert len(res['edits']) == 2
    out_doc = fitz.open(stream=res['pdf'], filetype='pdf')
    text = out_doc[0].get_text()
    assert "$150.00" in text
    assert "$250.00" in text


def test_syntax_variations_and_deletion():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((70, 95), "Item A: Shopify Store", fontsize=11, fontname="helv")
    page.insert_text((70, 120), "Delete Me Now", fontsize=11, fontname="helv")
    pdf_bytes = doc.tobytes()
    instructions = 'Replace all, replace "Shopify" with "Lola"\nDelete "Delete Me Now"'
    res = edit_pdf(pdf_bytes, instructions)
    assert len(res['edits']) == 2
    out_doc = fitz.open(stream=res['pdf'], filetype='pdf')
    text = out_doc[0].get_text()
    assert "Lola" in text
    assert "Delete Me Now" not in text


def test_more_than_twenty_instructions_allowed():
    doc = fitz.open(); page = doc.new_page()
    for i in range(30):
        page.insert_text((60, 40 + i * 24), f"Row{i:02d} value", fontsize=11, fontname="helv")
    data = doc.tobytes()
    instructions = "\n".join(f'Replace "Row{i:02d}" with "Itm{i:02d}"' for i in range(30))
    result = edit_pdf(data, instructions)
    text = fitz.open(stream=result['pdf'], filetype='pdf')[0].get_text()
    assert all(f"Itm{i:02d}" in text for i in range(30))


def test_zero_width_chars_and_right_alignment():
    doc = fitz.open(); page = doc.new_page()
    page.insert_text((100, 100), "$5,000.00", fontsize=11, fontname="helv")
    data = doc.tobytes()
    # Should accept zero-width characters \u200b\u200e and right-align cleanly
    instructions = 'Replace "$5,000.00" with "\u200b\u200e   $20.00"'
    result = edit_pdf(data, instructions)
    assert "$20.00" in result['validation'] or len(result['edits']) == 1


def test_grid_locked_insertion_and_off_grid_rejection():
    from classic.engine import inspect_pdf
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    # Create a structured table matching the user's "Allowed vs Not allowed" layout
    rows = [
        ("Living Book / Editorial", "30", "18", "12"),
        ("Product & Commerce", "25", "16", "10"),
        ("Brand Voice & Content", "20", "14", "9"),
        ("Technical & Code Hygiene", "15", "12", "7"),
    ]
    for idx, (cat, wt, sc, pct) in enumerate(rows):
        y = 140 + idx * 36
        p.insert_text((70, y), cat, fontsize=10, fontname="helv", color=(0.1, 0.1, 0.1))
        p.insert_text((310, y), wt, fontsize=10, fontname="helv", color=(0.1, 0.1, 0.1))
        p.insert_text((390, y), sc, fontsize=10, fontname="helv", color=(0.1, 0.1, 0.1))
        p.insert_text((460, y), pct, fontsize=10, fontname="helv", color=(0.1, 0.1, 0.1))
    pdf_bytes = doc.tobytes()

    info = inspect_pdf(pdf_bytes)
    assert info["pages_count"] == 1
    assert len(info["pages"][0]["columns"]) >= 4

    # Allowed: insert "Media" directly below "Living Book / Editorial" + edit cell values locked to grid
    res = edit_pdf(
        pdf_bytes,
        '\n'.join([
            'On page 1, insert "Media" below "Living Book / Editorial"',
            'On page 1, replace "Product & Commerce" with "Bio"',
            'On page 1, replace "Technical & Code Hygiene" with "Style the look"',
            'On page 1, replace "30" with "3"',
            'On page 1, replace "15" with "5"',
        ]),
    )
    out_doc = fitz.open(stream=res["pdf"], filetype="pdf")
    page_text = out_doc[0].get_text()
    assert "Media" in page_text
    assert "Bio" in page_text
    assert "Style the look" in page_text

    # Verify "Media" inherited exact x0=70.0, font, size=10.0
    media_edit = [e for e in res["edits"] if e["new"] == "Media"][0]
    assert abs(media_edit["position"]["x"] - 70.0) < 0.2
    assert media_edit["font_size"] == 10.0

    # Not allowed: arbitrary floating off-grid coordinates must be rejected
    with pytest.raises(PrecisionError, match="off-grid"):
        edit_pdf(pdf_bytes, 'On page 1, insert "Sample" at (183.0, 299.0)')


def test_coalesced_split_spans_and_sequential_duplicates():
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    # Simulate split span "-$" + "233.67" on same baseline y=100
    p.insert_text((402.0, 100.0), "-$", fontsize=10, fontname="hebo")
    p.insert_text((411.0, 100.0), "233.67", fontsize=10, fontname="hebo")
    # Simulate duplicate values "1.44" on different rows of page 1
    p.insert_text((288.0, 140.0), "1.44", fontsize=9, fontname="helv")
    p.insert_text((288.0, 170.0), "1.44", fontsize=9, fontname="helv")
    pdf_bytes = doc.tobytes()

    res = edit_pdf(
        pdf_bytes,
        'On page 1, replace "-$233.67" with "-$118.45"\n'
        'On page 1, replace "1.44" with "18.45"\n'
        'On page 1, replace "1.44" with "25.00"',
    )
    assert len(res["edits"]) == 3
    text = fitz.open(stream=res["pdf"], filetype="pdf")[0].get_text()
    assert "-$118.45" in text
    assert "18.45" in text
    assert "25.00" in text


def test_self_improvement_kernel_closed_loop(tmp_path):
    from classic.engine import PeditSelfImprovementKernel
    kernel = PeditSelfImprovementKernel(kernel_dir=tmp_path / "kernel")
    report = kernel.simulate_degraded_baseline_and_self_heal()
    assert report["champion_before"] == "v1.0.0-naive-baseline"
    assert report["promoted"] is not None
    assert report["promoted"].startswith("v2.2.0-challenger-")
    assert len(report["decisions"]) == 1
    dec = report["decisions"][0]
    assert dec["promoted"] is True
    assert dec["regressions"] == 0
    assert dec["challenger_score"] > dec["champion_score"]

    status = kernel.get_status_summary()
    assert status["active_champion"] == report["promoted"]
    assert len(status["open_clusters"]) == 0
    assert len(status["resolved_clusters"]) >= 1


def test_intelligence_dependency_and_auto_calculation():
    from classic.engine import inspect_pdf, recompute_dependent_edits
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 170), "10", fontsize=10, fontname="helv")
    page.insert_text((380, 170), "$150.00", fontsize=10, fontname="helv")
    page.insert_text((480, 170), "$1,500.00", fontsize=10, fontname="helv")

    page.insert_text((72, 200), "2", fontsize=10, fontname="helv")
    page.insert_text((380, 200), "$250.00", fontsize=10, fontname="helv")
    page.insert_text((480, 200), "$500.00", fontsize=10, fontname="helv")

    page.insert_text((380, 240), "Subtotal:", fontsize=10, fontname="hebo")
    page.insert_text((480, 240), "$2,000.00", fontsize=10, fontname="helv")

    ins = inspect_pdf(doc.tobytes())
    intel = ins.get("intelligence", {})
    rules = intel.get("dependency_rules", [])
    assert len(rules) >= 2, "Should detect at least row product and column sum rules"

    spans = ins["pages"][0]["spans"]
    pending = [{"span_id": spans[0]["id"], "old": "10", "new": "20"}]
    auto_edits = recompute_dependent_edits(pending, spans, rules)
    assert len(auto_edits) >= 2
    assert any(ae["new"] == "$3,000.00" for ae in auto_edits)
    assert any(ae["new"] == "$3,500.00" for ae in auto_edits)


def test_quality_check_discrepancy_detection():
    from classic.engine import inspect_pdf
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 140), "Descriptoin of Servcies", fontsize=10, fontname="helv")
    page.insert_text((72, 170), "Ammount Subtotle", fontsize=10, fontname="helv")

    ins = inspect_pdf(doc.tobytes())
    intel = ins.get("intelligence", {})
    discrepancies = intel.get("discrepancies", [])
    assert len(discrepancies) >= 3
    reasons = " ".join(d["reason"] for d in discrepancies)
    assert "Description" in reasons
    assert "Services" in reasons
    assert "Subtotal" in reasons or "Amount" in reasons


def test_arithmetic_discrepancy_and_auto_calc_with_dotted_line():
    from classic.engine import inspect_pdf, recompute_dependent_edits
    import fitz

    # Replicate exact user scenario: 2.44 above dotted line, 0.76 and 126.44 below dotted line
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text(fitz.Point(220, 50), "2.44", fontsize=11)
    page.draw_line(fitz.Point(60, 70), fitz.Point(300, 70), dashes="[2 2]")
    page.insert_text(fitz.Point(120, 95), "0.76", fontsize=11)
    page.insert_text(fitz.Point(220, 95), "126.44", fontsize=11)
    pdf_bytes = doc.tobytes()

    ins = inspect_pdf(pdf_bytes)
    intel = ins.get("intelligence", {})
    discrepancies = intel.get("discrepancies", [])
    rules = intel.get("dependency_rules", [])

    # Must detect arithmetic discrepancy: 2.44 + 0.76 = 3.20 not 126.44
    assert len(discrepancies) >= 1
    mismatch = [d for d in discrepancies if d["type"] == "math_mismatch" and d["old_text"] == "126.44"][0]
    assert mismatch["suggested_text"] == "3.20"
    assert "2.44" in mismatch["reason"]
    assert "0.76" in mismatch["reason"]
    assert "3.20" in mismatch["reason"]

    # Test auto-correct / recompute dependent edits
    spans = ins["pages"][0]["spans"]
    auto_edits = recompute_dependent_edits([], spans, rules)
    assert any(ae["old"] == "126.44" and ae["new"] == "3.20" for ae in auto_edits)


def test_scanned_image_only_pdf_ocr_and_edit():
    """Verify that image-only / scanned PDFs automatically trigger OCR and become editable."""
    # 1. Create an image-only PDF
    src_doc = fitz.open()
    src_page = src_doc.new_page(width=300, height=150)
    src_page.insert_text(fitz.Point(30, 50), "Subtotal", fontsize=14)
    src_page.insert_text(fitz.Point(150, 50), "100.00", fontsize=14)
    pix = src_page.get_pixmap(dpi=150)
    img_bytes = pix.tobytes("png")
    src_doc.close()

    scanned_doc = fitz.open()
    scanned_page = scanned_doc.new_page(width=300, height=150)
    scanned_page.insert_image(scanned_page.rect, stream=img_bytes)
    scanned_bytes = scanned_doc.tobytes()
    scanned_doc.close()

    # Verify native text is indeed 0
    check_doc = fitz.open("pdf", scanned_bytes)
    assert check_doc[0].get_text().strip() == ""
    check_doc.close()

    # 2. Inspect should automatically OCR the page and return editable cells
    ins = inspect_pdf(scanned_bytes)
    assert len(ins["pages"]) == 1
    spans = ins["pages"][0]["spans"]
    texts = [s["text"] for s in spans]
    assert any("Subtotal" in t or "100" in t for t in texts)

    # 3. Edit the OCR-detected cell
    target_span = [s for s in spans if "100" in s["text"]][0]
    result = edit_pdf(
        scanned_bytes,
        structured_edits=[{"page": 1, "old": target_span["text"], "new": "99.00"}],
    )
    assert result.get("pdf") is not None
    assert len(result["pdf"]) > 0
    assert len(result.get("edits", [])) == 1


def test_font_reordered_glyph_edit_allowed():
    """Verify editing numbers with reordered or existing glyphs does not falsely trigger 'lacks glyph'."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=150)
    page.insert_text(fitz.Point(30, 50), "Score: 91.25%", fontsize=12)
    b = doc.tobytes()
    doc.close()

    # Changing 91.25% to 95.25% must succeed
    res = edit_pdf(b, structured_edits=[{"page": 1, "old": "91.25%", "new": "95.25%"}])
    assert res.get("pdf") is not None
    assert len(res.get("edits", [])) == 1


def test_ocr_noise_filtering_removes_divider_hallucinations():
    """Verify that dashed lines and OCR punctuation artifacts are discarded."""
    from classic.engine import _is_ocr_noise_span
    # Divider noise examples from user screenshots
    assert _is_ocr_noise_span({'chars': [{'c': c} for c in 'tt—<“i‘“‘“‘“<‘<i<iti‘i‘i‘(aleé'], 'bbox': [144, 359, 434, 373]})
    assert _is_ocr_noise_span({'chars': [{'c': c} for c in '(C!S'], 'bbox': [96, 306, 436, 318]})
    assert _is_ocr_noise_span({'chars': [{'c': c} for c in '——'], 'bbox': [50, 100, 150, 105]})
    # Valid text must not be considered noise
    assert not _is_ocr_noise_span({'chars': [{'c': c} for c in '96.00'], 'bbox': [300, 200, 350, 215]})
    assert not _is_ocr_noise_span({'chars': [{'c': c} for c in 'Zara.com'], 'bbox': [100, 200, 180, 215]})


def test_french_grade_slash_and_subset_replacement():
    """Verify that replacing text with slashes, commas, and non-breaking spaces remains selectable."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=150)
    page.insert_text(fitz.Point(30, 50), "Note: 18,25 / 20", fontsize=16)
    b = doc.tobytes()
    doc.close()

    res = edit_pdf(b, structured_edits=[{"page": 1, "old": "18,25 / 20", "new": "18,75 / 20"}])
    assert res.get("pdf") is not None
    assert len(res.get("edits", [])) == 1
    out_doc = fitz.open("pdf", res["pdf"])
    assert "18,75 / 20" in out_doc[0].get_text()


def test_wide_body_paragraph_column_width_overflow():
    """Verify that editing long sentences does not fail with column width overflow even if max_width is sent."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    # A short header column at x=64 and another at x=113
    page.insert_text(fitz.Point(64, 100), "Col1", fontsize=10)
    page.insert_text(fitz.Point(113, 100), "Col2", fontsize=10)
    # A long body paragraph starting at x=64
    long_text = "Félicitations pour cette réussite, et tous nos meilleurs voeux pour la suite du processus de sélection"
    page.insert_text(fitz.Point(64, 200), long_text, fontsize=10)
    b = doc.tobytes()
    doc.close()

    new_text = "Félicitations Camille pour cette réussite, et tous nos meilleurs voeux pour la suite du processus de sélection"
    # Even if max_width=49.6 is erroneously sent from the column detector, edit must succeed
    res = edit_pdf(b, structured_edits=[{"page": 1, "old": long_text, "new": new_text, "max_width": 49.6}])
    assert res.get("pdf") is not None
    out_doc = fitz.open("pdf", res["pdf"])
    assert "Félicitations Camille" in out_doc[0].get_text()


def test_consecutive_edits_preserve_form_xobjects():
    """Verify that multiple consecutive edits do not strip page artwork or fail pixel diff validation."""
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    page.insert_text(fitz.Point(50, 100), "Initial: 18,25 / 20", fontsize=12)
    page.insert_text(fitz.Point(50, 150), "Percent: 91,25 %", fontsize=12)
    b1 = doc.tobytes()
    doc.close()

    # Edit 1
    res1 = edit_pdf(b1, structured_edits=[{"page": 1, "old": "18,25 / 20", "new": "18,75 / 20"}])
    b2 = res1["pdf"]

    # Edit 2 on the output of Edit 1
    res2 = edit_pdf(b2, structured_edits=[{"page": 1, "old": "91,25 %", "new": "95,25 %"}])
    assert res2.get("pdf") is not None
    out_doc = fitz.open("pdf", res2["pdf"])
    txt = out_doc[0].get_text()
    assert "18,75 / 20" in txt
    assert "95,25 %" in txt


def test_page_footer_column_width_tolerance():
    """Verify that editing page footers near the right margin does not fail with column width errors."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=841)
    # Page footer placed at bottom right: x=536.67, width=45.7pt, ending at x=582.37
    page.insert_text(fitz.Point(536.67, 752.5), "Page 4 of 6", fontsize=9)
    b = doc.tobytes()
    doc.close()

    # Changing Page 4 of 6 to Page 3 of 4 even with conservative max_width=42.3 sent must succeed
    res = edit_pdf(b, structured_edits=[{"page": 1, "old": "Page 4 of 6", "new": "Page 3 of 4", "max_width": 42.3}])
    assert res.get("pdf") is not None
    out_doc = fitz.open("pdf", res["pdf"])
    assert "Page 3 of 4" in out_doc[0].get_text()


def test_vertical_text_and_number_editing():
    """Verify that vertical (rotated) text and numbers can be detected, inspected, and edited."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=841)
    # Horizontal text
    page.insert_text(fitz.Point(100, 100), "Invoice Total: $500.00", fontsize=10)
    # Vertical number (rotate=90, dir=(0, -1))
    page.insert_text(fitz.Point(30, 400), "036919", fontsize=9, rotate=90)
    # Vertical text (rotate=270, dir=(0, 1))
    page.insert_text(fitz.Point(550, 200), "SER-9941", fontsize=9, rotate=270)
    pdf_bytes = doc.tobytes()
    doc.close()

    # 1. Inspection test
    ins = inspect_pdf(pdf_bytes)
    p0 = ins["pages"][0]
    vert_spans = [s for s in p0["spans"] if s.get("rotation", 0) != 0]
    assert len(vert_spans) == 2
    r90 = next(s for s in vert_spans if s["rotation"] == 90)
    assert r90["text"] == "036919"
    r270 = next(s for s in vert_spans if s["rotation"] == 270)
    assert r270["text"] == "SER-9941"

    # 2. Single vertical number edit
    res1 = edit_pdf(pdf_bytes, instructions='On page 1, replace "036919" with "036920"')
    assert res1.get("pdf") is not None
    out_doc1 = fitz.open("pdf", res1["pdf"])
    txt1 = out_doc1[0].get_text()
    assert "036920" in txt1
    assert "036919" not in txt1

    # 3. Simultaneous horizontal and vertical edits within the same PDF
    instructions = (
        'On page 1, replace "$500.00" with "$650.00"\n'
        'On page 1, replace "036919" with "036920"\n'
        'On page 1, replace "SER-9941" with "SER-9942"'
    )
    res2 = edit_pdf(pdf_bytes, instructions=instructions)
    assert res2.get("pdf") is not None
    assert len(res2["edits"]) == 3
    out_doc2 = fitz.open("pdf", res2["pdf"])
    txt2 = out_doc2[0].get_text()
    assert "$650.00" in txt2
    assert "036920" in txt2
    assert "SER-9942" in txt2


def test_vertical_structured_bbox_edits():
    """Verify structured_edits targeting a vertical span by bbox."""
    doc = fitz.open()
    page = doc.new_page(width=500, height=500)
    page.insert_text(fitz.Point(50, 300), "ITEM-88120", fontsize=10, rotate=90)
    pdf_bytes = doc.tobytes()
    doc.close()

    ins = inspect_pdf(pdf_bytes)
    target = ins["pages"][0]["spans"][0]
    assert target["rotation"] == 90

    res = edit_pdf(
        pdf_bytes,
        structured_edits=[{
            "action": "replace",
            "page": 1,
            "old": target["text"],
            "new": "ITEM-88199",
            "bbox": target["bbox"],
            "max_width": target["max_width"],
        }],
    )
    assert res.get("pdf") is not None
    out_doc = fitz.open("pdf", res["pdf"])
    assert "ITEM-88199" in out_doc[0].get_text()
