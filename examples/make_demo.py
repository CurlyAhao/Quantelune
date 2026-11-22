"""Regenerate the synthetic task-time CSV using Python's standard library."""
import csv
from pathlib import Path
from random import Random


def main():
    random = Random(218)
    destination = Path(__file__).with_name("task-time") / "task-time.csv"
    destination.parent.mkdir(exist_ok=True)
    groups = ["brief", "example"] * 12
    with destination.open("w", newline="", encoding="utf-8") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["session_id", "instruction", "completion_seconds"])
        for index, group in enumerate(groups, 1):
            duration = (58 if group == "brief" else 50) + random.randint(-15, 15)
            writer.writerow([f"S{index:02}", group, "" if index == 7 else duration])


if __name__ == "__main__":
    main()
