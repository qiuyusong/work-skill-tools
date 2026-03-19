import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fill_timereport


def make_commit(repo_name: str, summary: str, short_hash: str, committed_at: int) -> fill_timereport.CommitItem:
    return fill_timereport.CommitItem(
        repo_name=repo_name,
        summary=summary,
        short_hash=short_hash,
        committed_at=committed_at,
    )


class FillTimereportTests(unittest.TestCase):
    def test_load_submission_history_reads_current_month_used_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            current_month_report = {
                "run_date": "2026-03-19",
                "daily_reports": [
                    {
                        "date": "2026-03-19",
                        "source": "nearby:2026-03-18",
                        "entries": [
                            {
                                "work_description": "used nearby commit",
                                "commits": [
                                    {"hash": "used123", "summary": "used nearby commit"},
                                ],
                            }
                        ],
                        "submit_result": {"state": "ok"},
                    }
                ],
            }
            previous_month_report = {
                "run_date": "2026-02-28",
                "daily_reports": [
                    {
                        "date": "2026-02-28",
                        "source": "nearby:2026-02-27",
                        "entries": [
                            {
                                "work_description": "older nearby commit",
                                "commits": [
                                    {"hash": "older456", "summary": "older nearby commit"},
                                ],
                            }
                        ],
                        "submit_result": {"state": "ok"},
                    }
                ],
            }
            (report_dir / "2026-03-19-ecp-timereport.json").write_text(
                json.dumps(current_month_report, ensure_ascii=False),
                encoding="utf-8",
            )
            (report_dir / "2026-02-28-ecp-timereport.json").write_text(
                json.dumps(previous_month_report, ensure_ascii=False),
                encoding="utf-8",
            )

            history = fill_timereport.load_submission_history(report_dir, dt.date(2026, 3, 19))

            self.assertEqual(history.used_hashes, {"used123"})
            self.assertIn("used nearby commit", history.used_descriptions)
            self.assertNotIn("older456", history.used_hashes)

    def test_pick_nearby_commit_skips_previously_used_records(self) -> None:
        repo = fill_timereport.RepoTarget(path=Path("repo-a"), display_name="repo-a")
        target_date = dt.date(2026, 3, 19)
        nearby_date = dt.date(2026, 3, 18)
        commits_by_date = {
            nearby_date: [
                make_commit("repo-a", "used nearby commit", "used123", 1),
                make_commit("repo-a", "fresh nearby commit", "fresh456", 2),
            ]
        }

        def collect_day_commits(_: fill_timereport.RepoTarget, date_value: dt.date) -> list[fill_timereport.CommitItem]:
            return commits_by_date.get(date_value, [])

        commit, source_date = fill_timereport.pick_nearby_commit(
            target_date=target_date,
            search_end_date=target_date,
            repos=[repo],
            nearby_days=2,
            collect_day_commits=collect_day_commits,
            existing_descriptions={"used nearby commit"},
            used_hashes={"used123"},
        )

        self.assertIsNotNone(commit)
        self.assertEqual(commit.short_hash, "fresh456")
        self.assertEqual(source_date, nearby_date)

    def test_extract_existing_total_hours_prefers_detail_row_hours(self) -> None:
        details = [
            {
                "workHours": "8",
                "nested": [
                    {"workHours": "3"},
                ],
            },
            {
                "workTime": "2",
            },
            {
                "actualWorktime": "1.5",
            },
            {
                "children": [
                    {"workHours": "0.5"},
                    {"workTime": "0.5"},
                ]
            },
        ]

        total = fill_timereport.extract_existing_total_hours(details)

        self.assertEqual(total, 12.5)

    def test_calculate_main_entity_hours_appends_manual_overtime(self) -> None:
        entry = fill_timereport.build_manual_entry(
            target_date=dt.date(2026, 3, 19),
            hours=2.0,
            activity_type="會議",
            activity_detail="顾问会议",
        )
        existing_details = [
            {"workHours": "8.0"},
        ]

        total = fill_timereport.calculate_main_entity_hours(
            entries=[entry],
            existing_details=existing_details,
            append_mode=True,
        )

        self.assertEqual(total, 10.0)


if __name__ == "__main__":
    unittest.main()
