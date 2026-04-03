"""
scripts/summarize_output.py
"""
import logging
from datetime import datetime
from pathlib import Path

from modules.result_scanner import AnswerCleaner, RegionResult, ResultJsonParser, RawJsonParser, ScanStatus
from modules.report_builder import DocxReportBuilder

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

# ── Настройки ────────────────────────────────────────────────────────────────
SCAN_DIR = Path(__file__).resolve().parent.parent / "data/honor_guardian_scan_2026/2026-04-02"
OUT_DIR  = Path.home() / "Desktop"
# ─────────────────────────────────────────────────────────────────────────────


def scan(scan_dir: Path) -> list[RegionResult]:
    if not scan_dir.exists():
        log.error("Папка не найдена: %s", scan_dir)
        return []

    results = []
    for region_dir in sorted(d for d in scan_dir.iterdir() if d.is_dir()):
        raw   = RawJsonParser.parse(region_dir / "raw.json")
        item  = RegionResult(region=region_dir.name, status=ScanStatus.FAILED,
                             channel=raw["channel"], posts_count=raw["posts_count"],
                             scan_date=raw["scan_date"])

        answer = ResultJsonParser.parse(region_dir / "result.json")
        if answer is None:
            item.error = "result.json отсутствует или повреждён"
        elif ResultJsonParser.is_negative(answer):
            item.status = ScanStatus.EMPTY
        else:
            item.status = ScanStatus.FOUND
            item.answer = AnswerCleaner.clean(answer)

        log.info("[%-6s]  %s  %s", item.status.name, item.region, item.channel)
        results.append(item)

    return results


def build_report(results: list[RegionResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    date     = datetime.now().strftime("%d.%m.%Y")
    out_path = out_dir / f"МониторингСМИ_{date}.docx"
    DocxReportBuilder(results=results, report_date=date).save(out_path)
    return out_path


if __name__ == "__main__":
    results = scan(SCAN_DIR)

    found  = sum(1 for r in results if r.status == ScanStatus.FOUND)
    empty  = sum(1 for r in results if r.status == ScanStatus.EMPTY)
    failed = sum(1 for r in results if r.status == ScanStatus.FAILED)
    log.info("Итого: %d регионов  |  ✔ %d  ✘ %d  – %d", len(results), found, failed, empty)

    out = build_report(results, OUT_DIR)