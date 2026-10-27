"""Original multi-section PDF through real local Foundation and Poppler.

Checks connected specification review, source locators, reopen and exports.
This is software integration evidence, not representative project acceptance.
"""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("drawing", ROOT / "scripts/drawing-workspace.py")
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


SECTIONS = (
    ("23 21 13", "HYDRONIC PIPING", "Provide isolation valves where shown."),
    ("23 09 23", "DIRECT DIGITAL CONTROLS", "Submit controls validation reports."),
    ("23 07 19", "PIPING INSULATION", "Do not insulate drain piping unless noted."),
    ("23 05 93.01", "TESTING ADJUSTING AND BALANCING", "Submit balancing reports after testing."),
    ("23 25 00", "HVAC WATER TREATMENT", None),
)


def original_pdf():
    contents = []
    for number, title, clause in SECTIONS:
        lines = ["ORIGINAL SYNTHETIC SPECIFICATION", "SECTION " + number + " - " + title]
        if clause:
            lines.append("1. " + clause)
        contents.append("".join("BT /F1 11 Tf 40 %s Td (%s) Tj ET\n" %
                                (740 - i * 60, text) for i, text in enumerate(lines)).encode())
    count = len(contents)
    font = 3 + count
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               ("<< /Type /Pages /Kids [%s] /Count %s >>" %
                (" ".join("%s 0 R" % (3 + i) for i in range(count)), count)).encode()]
    for i in range(count):
        objects.append(("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        "/Resources << /Font << /F1 %s 0 R >> >> /Contents %s 0 R >>" %
                        (font, font + 1 + i)).encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objects.extend(b"<< /Length " + str(len(c)).encode() + b" >>\nstream\n" + c + b"endstream" for c in contents)
    data, offsets = bytearray(b"%PDF-1.7\n%\x80\x81\x82\x83\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(("xref\n0 %s\n0000000000 65535 f \n" % (len(objects) + 1)).encode())
    for offset in offsets:
        data.extend(("%010d 00000 n \n" % offset).encode())
    data.extend(("trailer\n<< /Size %s /Root 1 0 R >>\nstartxref\n%s\n%%%%EOF\n" %
                 (len(objects) + 1, xref)).encode())
    return bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--heleos", type=Path, required=True)
    parser.add_argument("--pdftotext", type=Path, required=True)
    args = parser.parse_args()
    reader = module._equipment_module.TextReader(args.pdftotext)
    workspace = module.Workspace([str(args.heleos)], args.workspace, text_reader=reader)
    try:
        raw = original_pdf()
        revision = hashlib.sha256(raw).hexdigest()
        workspace.import_pdf(io.BytesIO(raw), len(raw), "Original mechanical specification.pdf")

        def command(action, values):
            return workspace.workflow.command(action, {"version": workspace.workflow.data["version"],
                "actor": "Original scope integration", "reason": "Original synthetic section-review check", "values": values})

        for i, (number, _, _) in enumerate(SECTIONS):
            command("page", {"source": {"revision_id": revision, "index": i}, "role": "specification",
                             "label": number, "building": "", "level": "", "note": "Original synthetic input"})
        command("read_documents", {})
        workspace.documents.thread.join(timeout=55)
        assert not workspace.documents.thread.is_alive(), "Reading did not finish"
        view = workspace.workflow.view()
        scope = view["mechanical_scope"]
        assert scope["state"] == "current", view["document_reading"]
        assert [r["section"] for r in scope["sections"]] == sorted(s[0] for s in SECTIONS)
        assert not scope["unread_pages"], scope["issues"]
        by_number = {r["section"]: r for r in scope["sections"]}
        for i, (number, _, clause) in enumerate(SECTIONS):
            row = by_number[number]
            heading = row["headings"][0]
            assert heading["source"]["revision_id"] == revision
            assert heading["source"]["index"] == i
            assert heading["source"]["bbox"][1] < .2
            if clause:
                assert len(row["requirements"]) == 1, row
                requirement = row["requirements"][0]
                assert clause in requirement["text"]
                assert requirement["source"]["index"] == i
            else:
                assert row["review_state"] == "no_requirements_identified"
        negative = by_number["23 07 19"]["requirements"][0]
        assert "negative" in negative["qualifiers"] and "conditional" in negative["qualifiers"]
        rule = by_number["23 21 13"]["requirements"][0]
        view = command("requirement_review", {"requirement_id": rule["id"], "disposition": "applicable"})
        scope = view["mechanical_scope"]
        assert next(r for r in scope["sections"] if r["section"] == "23 21 13")["counts"]["applicable"] == 1
        export = workspace.workflow.export()
        with zipfile.ZipFile(io.BytesIO(export)) as archive:
            assert json.loads(archive.read("mechanical-scope.json")) == scope
            assert b"23 05 93.01" in archive.read("mechanical-sections.csv")
            assert b"Submit controls validation reports" in archive.read("mechanical-requirements.csv")
        workspace.close()
        workspace = module.Workspace([str(args.heleos)], args.workspace, text_reader=reader)
        assert workspace.workflow.view()["mechanical_scope"] == scope
        assert not scope["project_coverage_verified"] and not scope["product_coverage_verified"]
        (args.workspace / "original-scope.pdf").write_bytes(raw)
        (args.workspace / "mechanical-scope-export.zip").write_bytes(export)
        (args.workspace / "mechanical-scope.json").write_text(json.dumps(scope, indent=2) + "\n")
        receipt = {"status": "PASS", "input_class": "original_synthetic", "revision_id": revision,
                   "sections": sorted(by_number), "requirements": 4, "heading_only_sections": 1,
                   "review_reopen_export": True, "source_locations_verified": True,
                   "real_foundation": True, "real_poppler": True, "live_model": False,
                   "native_windows": False, "representative_acceptance": False,
                   "reader": reader.identity(), "cli_sha256": module._equipment_module.file_digest(args.heleos)}
        (args.workspace / "acceptance.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps(receipt))
    finally:
        workspace.close()


if __name__ == "__main__":
    main()
