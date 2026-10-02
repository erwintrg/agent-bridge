"""Read-only demo job: prints made-up calendar events for the next N days. Real jobs would call your calendar."""
import sys

EVENTS = [
    ("Thu 01.10", ["10:00-10:30 Team sync (video call)", "15:00-15:45 Review of the onboarding flow"]),
    ("Fri 02.10", ["14:00-15:00 Example Corp discovery call"]),
    ("Sat 03.10", []),
    ("Sun 04.10", []),
    ("Mon 05.10", ["09:30-10:00 Weekly planning", "16:00-17:00 Proposal writing block"]),
    ("Tue 06.10", ["11:00-11:30 Dentist (personal)"]),
    ("Wed 07.10", ["17:00 Example Corp proposal due"]),
]

days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
print(f"Calendar, next {days} days (fixture data):")
for day, items in EVENTS[:days]:
    print(f"{day}: " + ("; ".join(items) if items else "nothing"))
