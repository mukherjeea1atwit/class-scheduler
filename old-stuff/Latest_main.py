"""
WIT Class Scheduler
Assigns faculty, rooms, and time slots to course sections
subject to scheduling constraints.
"""
import contextlib
import csv
import io
import json
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import time
from typing import Dict, List, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────

ALL_DAYS        = ["M", "T", "W", "Th", "F"]
LECTURE_PATTERNS = [["M", "W"], ["T", "Th"], ["W", "F"]]
GRAD_START_HR   = 17        # 5 PM — earliest start for grad courses
GRAD_END_HR     = 20        # 8 PM — latest start for grad courses
FACULTY_GAP_MIN = 15        # min gap between back-to-back classes for same faculty
RESERVED_START  = 12 * 60   # Tue/Thu 12:00 reserved (minutes from midnight)
RESERVED_END    = 13 * 60 + 30  # Tue/Thu 13:30
AM_CUTOFF_HR    = 12        # hours before this = AM
AM_TARGET_RATIO = 0.60      # 60 % of undergrad meetings should be AM


# ──────────────────────────────────────────────────────────────────────────────
# DATA MODELS
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Course:
    number: str
    name: str
    lecture_days_per_week: int
    lecture_hours: int
    lab_hours: int
    sections: int
    preferred_room: Optional[str] = None


@dataclass
class Section:
    id: str
    course_number: str
    course_name: str
    lecture_days_per_week: int
    lecture_hours: int
    lab_hours: int
    preferred_room: Optional[str]
    faculty_options: List[str] = field(default_factory=list)


@dataclass
class Room:
    name: str
    type: str
    capacity: int


@dataclass
class TimeSlot:
    start: time
    stop: time
    duration_min: int
    label: str
    evening: bool
    days_allowed: List[str]


@dataclass
class RoomPreference:
    course: str
    type: str
    rank: int
    location: str
    max_cap: int


@dataclass
class ScheduledSection:
    section_id: str
    course_number: str
    course_name: str
    faculty: str
    room: Optional[str]
    days: List[str]
    start_time: Optional[time]
    end_time: Optional[time]
    has_lab: bool
    is_lab: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def to_int(s: str, default: int = 0) -> int:
    try:
        return int((s or "").strip())
    except (ValueError, AttributeError):
        return default


def parse_time(s: str) -> time:
    h, m, sec = (int(x) for x in s.strip().split(":"))
    return time(h, m, sec)


def split_csv(s: str) -> List[str]:
    return [p.strip() for p in (s or "").split(",") if p.strip()]


def t2m(t: time) -> int:
    """Convert a time object to minutes since midnight."""
    return t.hour * 60 + t.minute


def normalize(course_number: str) -> str:
    return (course_number or "").replace(" ", "").strip()


def is_grad(course_number: str) -> bool:
    m = re.search(r"(\d+)", course_number or "")
    return bool(m and int(m.group(1)) >= 5000)


def course_level(course_number: str) -> int:
    m = re.search(r"(\d+)", course_number or "")
    return int(m.group(1)) if m else 0


def schedule_priority(sec: Section) -> int:
    """Lower number = scheduled first.  Upper-UG → grad → lower-UG."""
    level = course_level(sec.course_number)
    if 3000 <= level < 5000:
        return 0
    if level >= 5000:
        return 1
    return 2


def per_meeting_min(total_min: int, num_days: int) -> int:
    return (total_min + num_days - 1) // num_days


def lecture_lab_minutes(lecture_hours: int, lab_hours: int) -> Tuple[int, int]:
    lec = 150 if lecture_hours == 3 else (240 if lecture_hours == 4 else lecture_hours * 60)
    lab = 105 if lab_hours == 2 else lab_hours * 60
    return lec, lab


def overlaps_reserved(days: List[str], start: time, end: time) -> bool:
    """True if this block falls on Tue/Thu and overlaps the 12:00–13:30 reserved window."""
    if not any(d in ("T", "Th") for d in days):
        return False
    s, e = t2m(start), t2m(end)
    return not (e <= RESERVED_START or s >= RESERVED_END)


def times_conflict(s1: int, e1: int, s2: int, e2: int, gap: int = FACULTY_GAP_MIN) -> bool:
    """True if two time ranges are closer than `gap` minutes."""
    return not (e1 + gap <= s2 or e2 + gap <= s1)


def preferred_lab_day(lecture_days: List[str]) -> str:
    """Choose the best single day for a lab given the lecture day pattern."""
    joined = "".join(lecture_days)
    mapping = {"MW": "F", "TTh": "F", "WF": "M"}
    if joined in mapping:
        return mapping[joined]
    for d in ALL_DAYS:
        if d not in lecture_days:
            return d
    return "F"


# ──────────────────────────────────────────────────────────────────────────────
# CSV LOADERS
# ──────────────────────────────────────────────────────────────────────────────

def load_courses(path: str) -> List[Course]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.append(Course(
                number=r["Course number"].strip(),
                name=r["Course Name"].strip(),
                lecture_days_per_week=to_int(r["lecture days per week"]),
                lecture_hours=to_int(r["lecture hours"]),
                lab_hours=to_int(r["lab hours"]),
                sections=to_int(r["number of sections"]),
                preferred_room=(r.get("Preferred Room") or "").strip() or None,
            ))
    return out


def load_faculty_preferences(path: str) -> Dict[str, List[str]]:
    """Returns {course_number: [ranked faculty list]}."""
    out: Dict[str, List[str]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            course = r["Course Number"].strip()
            fac_str = r.get("Faculty") or r.get("faculty") or ""
            out[course] = split_csv(fac_str)
    return out


def load_rooms(path: str) -> List[Room]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.append(Room(
                name=r["Room"].strip(),
                type=r["Type"].strip(),
                capacity=to_int(r["Capacity"]),
            ))
    return out


def load_timeslots(path: str) -> List[TimeSlot]:
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            out.append(TimeSlot(
                start=parse_time(r["start_time"]),
                stop=parse_time(r["stop_time"]),
                duration_min=to_int(r["duration_min"]),
                label=r["slot_label"].strip(),
                evening=(r["evening"] or "").strip().lower() in ("true", "1", "yes"),
                days_allowed=split_csv(r["Days Allowed"].strip().strip('"')),
            ))
    return out


def load_faculty_loads(path: str) -> Dict[str, int]:
    """Returns {faculty_name: max_course_load}."""
    out: Dict[str, int] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            name = (r.get("Faculty") or "").strip()
            if name:
                out[name] = to_int(r.get("CS Course Load", "0"))
    return out


def load_room_preferences(path: str) -> Dict[Tuple[str, str], List[RoomPreference]]:
    """Returns {(normalized_course, type_lower): [RoomPreference sorted by rank]}."""
    out: Dict[Tuple[str, str], List[RoomPreference]] = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            course = normalize(r["Course"])
            rtype = (r["Type"] or "").strip()
            key = (course, rtype.lower())
            pref = RoomPreference(
                course=course,
                type=rtype,
                rank=to_int(r["PreferenceRank"]),
                location=(r["Location"] or "").strip(),
                max_cap=to_int(r.get("max_cap", "0")),
            )
            out.setdefault(key, []).append(pref)
    for lst in out.values():
        lst.sort(key=lambda p: p.rank)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# SECTION BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def build_sections(courses: List[Course], faculty_prefs: Dict[str, List[str]]) -> List[Section]:
    sections: List[Section] = []
    for course in courses:
        if course.sections == 0:
            continue
        fac = faculty_prefs.get(course.number, [])
        for i in range(1, course.sections + 1):
            sections.append(Section(
                id=f"{course.number}-{i}",
                course_number=course.number,
                course_name=course.name,
                lecture_days_per_week=course.lecture_days_per_week,
                lecture_hours=course.lecture_hours,
                lab_hours=course.lab_hours,
                preferred_room=course.preferred_room,
                faculty_options=fac,
            ))
    return sections


# FacultyAssigner removed — faculty selection is now integrated into build_schedule
# so that time, room, and faculty constraints are satisfied jointly.


# ──────────────────────────────────────────────────────────────────────────────
# ROOM ASSIGNER
# ──────────────────────────────────────────────────────────────────────────────

class RoomAssigner:
    """Tracks room availability and assigns rooms to sections."""

    def __init__(self, rooms: List[Room], room_prefs: Dict[Tuple[str, str], List[RoomPreference]]):
        self.rooms = rooms
        self.room_prefs = room_prefs
        self._booked: Dict[str, Dict[str, List[Tuple[int, int]]]] = {}

    def is_free(self, room: str, days: List[str], start: time, end: time) -> bool:
        s, e = t2m(start), t2m(end)
        for d in days:
            for (bs, be) in self._booked.get(room, {}).get(d, []):
                if not (e <= bs or s >= be):
                    return False
        return True

    def _book(self, room: str, days: List[str], start: time, end: time) -> None:
        s, e = t2m(start), t2m(end)
        entry = self._booked.setdefault(room, {})
        for d in days:
            entry.setdefault(d, []).append((s, e))

    def find_room(
        self,
        sec: Section,
        days: List[str],
        start: time,
        end: time,
        *,
        is_lab: bool,
        needed_capacity: int = 25,
    ) -> Optional[str]:
        """Return the best available room name without booking it. None only if no rooms exist."""
        needed_type = "lab" if is_lab else "lecture"
        key = (normalize(sec.course_number), needed_type)

        for pref in self.room_prefs.get(key, []):
            cap = pref.max_cap or needed_capacity
            for room in self.rooms:
                if room.name == pref.location and room.capacity >= cap and self.is_free(room.name, days, start, end):
                    return room.name

        free_candidates = sorted(
            (r for r in self.rooms if r.capacity >= needed_capacity and self.is_free(r.name, days, start, end)),
            key=lambda r: r.capacity,
        )
        if free_candidates:
            return free_candidates[0].name

        if self.rooms:
            worst = min(self.rooms, key=lambda r: r.capacity)
            print(
                f"[ROOM-OVERBOOK] {sec.id} on {days} "
                f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')} → {worst.name}"
            )
            return worst.name

        return None

    def book_room(self, room: str, days: List[str], start: time, end: time) -> None:
        """Commit a room booking found via find_room."""
        self._book(room, days, start, end)

    def find_and_book(
        self,
        sec: Section,
        days: List[str],
        start: time,
        end: time,
        *,
        is_lab: bool,
        needed_capacity: int = 25,
    ) -> Optional[str]:
        room = self.find_room(sec, days, start, end, is_lab=is_lab, needed_capacity=needed_capacity)
        if room:
            self.book_room(room, days, start, end)
        return room


# ──────────────────────────────────────────────────────────────────────────────
# TIME SLOT SCHEDULER
# ──────────────────────────────────────────────────────────────────────────────

class TimeSlotScheduler:
    """Tracks faculty and slot-load availability; finds and books time slots."""

    def __init__(self, timeslots: List[TimeSlot]):
        self.slots = sorted(timeslots, key=lambda t: (t.start.hour, t.start.minute))
        self._faculty_busy: Dict[str, Dict[str, List[Tuple[int, int]]]] = {}
        self._slot_load: Dict[str, Dict[str, int]] = {d: {} for d in ALL_DAYS}

    # ── public interface ────────────────────────────────────────────

    def find_slot(
        self,
        sec: Section,
        faculty: str,
        days: List[str],
        min_duration: int,
        *,
        force_pm: bool = False,
        max_duration: Optional[int] = None,
    ) -> Optional[TimeSlot]:
        candidates = self._eligible_slots(sec, min_duration, force_pm=force_pm, max_duration=max_duration)
        ordered = sorted(candidates, key=lambda t: (self._busyness(t, days), t.start.hour, t.start.minute))

        for slot in ordered:
            if overlaps_reserved(days, slot.start, slot.stop):
                continue
            if not self._slot_capacity_ok(days, slot):        # C11
                continue
            if not self._faculty_free(faculty, days, slot.start, slot.stop):
                continue
            if self._would_exceed_span(faculty, days, slot.start, slot.stop):  # C2
                continue
            return slot
        return None

    def book(self, faculty: str, days: List[str], slot: TimeSlot) -> None:
        self._block_faculty(faculty, days, slot.start, slot.stop)
        self._increment_load(days, slot)

    @property
    def slot_load(self) -> Dict[str, Dict[str, int]]:
        return self._slot_load

    # ── private helpers ─────────────────────────────────────────────

    def _slot_key(self, slot: TimeSlot) -> str:
        return f"{slot.start.strftime('%H:%M')}-{slot.stop.strftime('%H:%M')}"

    def _eligible_slots(
        self,
        sec: Section,
        min_duration: int,
        *,
        force_pm: bool,
        max_duration: Optional[int] = None,
    ) -> List[TimeSlot]:
        def dur_ok(t: TimeSlot) -> bool:
            return t.duration_min >= min_duration and (max_duration is None or t.duration_min <= max_duration)

        if is_grad(sec.course_number):
            return [t for t in self.slots if GRAD_START_HR <= t.start.hour < GRAD_END_HR and dur_ok(t)]
        slots = [t for t in self.slots if t.start.hour < GRAD_START_HR and dur_ok(t)]
        if force_pm:
            slots = [t for t in slots if t.start.hour >= AM_CUTOFF_HR]
        return slots

    def _faculty_free(self, faculty: str, days: List[str], start: time, end: time) -> bool:
        if faculty == "TBA":
            return True
        s, e = t2m(start), t2m(end)
        busy = self._faculty_busy.get(faculty, {})
        for d in days:
            for (bs, be) in busy.get(d, []):
                if times_conflict(s, e, bs, be):
                    return False
        return True

    def _block_faculty(self, faculty: str, days: List[str], start: time, end: time) -> None:
        if faculty == "TBA":
            return
        s, e = t2m(start), t2m(end)
        entry = self._faculty_busy.setdefault(faculty, {})
        for d in days:
            entry.setdefault(d, []).append((s, e))

    def _slot_capacity_ok(self, days: List[str], slot: TimeSlot) -> bool:
        key = self._slot_key(slot)
        return all(self._slot_load[d].get(key, 0) < 10 for d in days)

    def _increment_load(self, days: List[str], slot: TimeSlot) -> None:
        key = self._slot_key(slot)
        for d in days:
            self._slot_load[d][key] = self._slot_load[d].get(key, 0) + 1

    def _busyness(self, slot: TimeSlot, days: List[str]) -> int:
        key = self._slot_key(slot)
        return max(self._slot_load[d].get(key, 0) for d in days)

    def _would_exceed_span(self, faculty: str, days: List[str], start: time, end: time) -> bool:
        """True if adding this block would push the faculty's teaching span > 9 h on any day."""
        if faculty == "TBA":
            return False
        s, e = t2m(start), t2m(end)
        busy = self._faculty_busy.get(faculty, {})
        for d in days:
            existing = busy.get(d, [])
            all_times = existing + [(s, e)]
            span_hr = (max(e2 for _, e2 in all_times) - min(s2 for s2, _ in all_times)) / 60
            if span_hr > 9:
                return True
        return False


# ──────────────────────────────────────────────────────────────────────────────
# SCHEDULER ORCHESTRATOR
# ──────────────────────────────────────────────────────────────────────────────

def build_schedule(
    sections: List[Section],
    fac_prefs: Dict[str, List[str]],
    faculty_limits: Dict[str, int],
    time_sched: TimeSlotScheduler,
    room_assigner: RoomAssigner,
) -> Dict[str, ScheduledSection]:
    """
    Jointly assigns faculty + day pattern + time slot + room for each section so that
    C2 (daily span), C4 (days/week), C11 (concurrency), and C16 (day balance) are
    all satisfied during assignment rather than flagged after the fact.
    """
    lectures: Dict[str, ScheduledSection] = {}
    labs: List[ScheduledSection] = []

    # ── integrated state ───────────────────────────────────────────────────────
    faculty_load: Dict[str, int] = {f: 0 for f in faculty_limits}
    faculty_days_map: Dict[str, set] = {}   # {faculty → set of days they teach}
    day_count: Dict[str, int] = {d: 0 for d in ALL_DAYS}  # sections per day (C16)

    # AM/PM balance (undergrad only)
    total_ug = sum(
        1 + (1 if s.lab_hours > 0 else 0)
        for s in sections if not is_grad(s.course_number)
    )
    max_am = math.ceil(AM_TARGET_RATIO * total_ug)
    am_used = 0

    ordered = sorted(sections, key=lambda s: (schedule_priority(s), s.course_number, s.id))

    # ── helpers scoped to this function ───────────────────────────────────────

    def max_load(fac: str) -> int:
        return faculty_limits.get(fac, 3)

    def can_assign(fac: str, sec: Section) -> bool:
        """Faculty has remaining load capacity and hasn't taught 2 sections of this course yet."""
        faculty_load.setdefault(fac, 0)
        if faculty_load[fac] >= max_load(fac):
            return False
        same = sum(
            1 for s in lectures.values()
            if s.faculty == fac and s.course_number == sec.course_number
        )
        return same < 2

    def faculty_candidates(sec: Section) -> List[str]:
        """Preferred faculty first (by rank), then all remaining with capacity."""
        seen: set = set()
        result: List[str] = []
        for f in fac_prefs.get(sec.course_number, []):
            if f not in seen:
                result.append(f)
                seen.add(f)
                faculty_load.setdefault(f, 0)
        for f in faculty_limits:
            if f not in seen:
                result.append(f)
                seen.add(f)
        return result

    def viable_patterns(fac: str) -> List[List[str]]:
        """
        Return LECTURE_PATTERNS that keep faculty ≤ 4 days (C4), sorted by how much
        load they'd add to already-busy days (lightest first, for C16 balance).
        Falls back to all patterns if C4 cannot be satisfied.
        """
        current = faculty_days_map.get(fac, set())
        ok, over = [], []
        for pattern in LECTURE_PATTERNS:
            score = sum(day_count.get(d, 0) for d in pattern)
            if len(current | set(pattern)) <= 4:
                ok.append((score, pattern))
            else:
                over.append((score, pattern))
        ok.sort(key=lambda x: x[0])
        over.sort(key=lambda x: x[0])
        return [p for _, p in ok] or [p for _, p in over]

    def best_lab_day(fac: str, lecture_days: List[str]) -> str:
        """
        Pick the lab day that:
          1. Is not a lecture day (C9)
          2. Keeps faculty ≤ 4 days (C4) if possible
          3. Is the least loaded day overall (C16 balance)
        """
        current = faculty_days_map.get(fac, set()) | set(lecture_days)
        non_lecture = [d for d in ALL_DAYS if d not in lecture_days]
        c4_ok = [d for d in non_lecture if len(current | {d}) <= 4]
        pool = c4_ok if c4_ok else non_lecture
        return min(pool, key=lambda d: day_count.get(d, 0))

    def _try_assign(
        sec: Section,
        fac: str,
        days: List[str],
        lec_min: int,
        force_pm: bool,
    ) -> Optional[Tuple[List[str], "TimeSlot", str]]:
        """Try to find a (days, slot, room) for one (faculty, pattern) combo. None if impossible."""
        per_day = per_meeting_min(lec_min, len(days))
        slot = time_sched.find_slot(sec, fac, days, per_day, force_pm=force_pm)
        if slot is None and force_pm:
            slot = time_sched.find_slot(sec, fac, days, per_day, force_pm=False)
        if slot is None:
            return None
        room = room_assigner.find_room(sec, days, slot.start, slot.stop, is_lab=False)
        if room is None:
            return None
        return (days, slot, room)

    # ── main scheduling loop ──────────────────────────────────────────────────

    for sec in ordered:
        lec_min, lab_min = lecture_lab_minutes(sec.lecture_hours, sec.lab_hours)
        force_pm = not is_grad(sec.course_number) and am_used >= max_am

        chosen: Optional[Tuple[str, List[str], "TimeSlot", str]] = None  # (fac, days, slot, room)

        # Search jointly over (faculty × day_pattern) until all constraints satisfied
        for fac in faculty_candidates(sec):
            if not can_assign(fac, sec):
                continue
            for days in viable_patterns(fac):
                result = _try_assign(sec, fac, days, lec_min, force_pm)
                if result:
                    chosen = (fac, *result)
                    break
            if chosen:
                break

        # Fallback: TBA faculty, any pattern
        if not chosen:
            print(f"[WARN] {sec.id}: No faculty satisfied all constraints; trying TBA.")
            for days in LECTURE_PATTERNS:
                result = _try_assign(sec, "TBA", days, lec_min, False)
                if result:
                    chosen = ("TBA", *result)
                    break

        # Hard fallback: force something rather than crash
        if not chosen:
            print(f"[CRITICAL] {sec.id}: No assignment found; forcing.")
            days_f = LECTURE_PATTERNS[0]
            per_day_f = per_meeting_min(lec_min, len(days_f))
            cands = time_sched._eligible_slots(sec, per_day_f, force_pm=False, max_duration=None)
            slot_f = cands[0] if cands else time_sched.slots[0]
            chosen = ("TBA", days_f, slot_f, "FORCE_ASSIGN_ROOM")

        fac, days, slot, room = chosen

        # Commit lecture
        room_assigner.book_room(room, days, slot.start, slot.stop)
        time_sched.book(fac, days, slot)
        faculty_load[fac] = faculty_load.get(fac, 0) + 1
        faculty_days_map.setdefault(fac, set()).update(days)
        for d in days:
            day_count[d] += 1
        if not is_grad(sec.course_number):
            am_used += 1 if slot.start.hour < AM_CUTOFF_HR else 0

        lectures[sec.id] = ScheduledSection(
            section_id=sec.id,
            course_number=sec.course_number,
            course_name=sec.course_name,
            faculty=fac,
            room=room,
            days=list(days),
            start_time=slot.start,
            end_time=slot.stop,
            has_lab=sec.lab_hours > 0,
            is_lab=False,
        )

        # ── lab ───────────────────────────────────────────────────────────────
        if sec.lab_hours > 0:
            force_pm_lab = not is_grad(sec.course_number) and am_used >= max_am
            lab_day = best_lab_day(fac, days)

            LAB_MAX_MIN = 130  # C7: labs must be ≤ 130 min
            lab_slot = time_sched.find_slot(sec, fac, [lab_day], lab_min, force_pm=force_pm_lab, max_duration=LAB_MAX_MIN)
            if lab_slot is None:
                lab_slot = time_sched.find_slot(sec, fac, [lab_day], lab_min, max_duration=LAB_MAX_MIN)

            # Try other non-lecture days if preferred lab day has no slot
            if lab_slot is None:
                for alt in ALL_DAYS:
                    if alt not in days:
                        lab_slot = time_sched.find_slot(sec, fac, [alt], lab_min, max_duration=LAB_MAX_MIN)
                        if lab_slot:
                            lab_day = alt
                            break

            if lab_slot is None:
                print(f"[CRITICAL] {sec.id}-LAB: No time slot found; forcing.")
                cands = time_sched._eligible_slots(sec, lab_min, force_pm=False, max_duration=LAB_MAX_MIN)
                lab_slot = cands[0] if cands else time_sched.slots[0]

            lab_room = room_assigner.find_room(sec, [lab_day], lab_slot.start, lab_slot.stop, is_lab=True)
            if lab_room is None:
                lab_room = "FORCE_ASSIGN_ROOM"
            else:
                room_assigner.book_room(lab_room, [lab_day], lab_slot.start, lab_slot.stop)

            time_sched.book(fac, [lab_day], lab_slot)
            faculty_days_map.setdefault(fac, set()).add(lab_day)
            day_count[lab_day] += 1
            if not is_grad(sec.course_number):
                am_used += 1 if lab_slot.start.hour < AM_CUTOFF_HR else 0

            labs.append(ScheduledSection(
                section_id=f"{sec.id}-LAB",
                course_number=sec.course_number,
                course_name=sec.course_name,
                faculty=fac,
                room=lab_room,
                days=[lab_day],
                start_time=lab_slot.start,
                end_time=lab_slot.stop,
                has_lab=False,
                is_lab=True,
            ))

    # Interleave labs right after their parent lecture
    result: Dict[str, ScheduledSection] = {}
    lab_by_parent = {lab.section_id.replace("-LAB", ""): lab for lab in labs}
    for sid, s in lectures.items():
        result[sid] = s
        if sid in lab_by_parent:
            lab = lab_by_parent[sid]
            result[lab.section_id] = lab

    return result


# ──────────────────────────────────────────────────────────────────────────────
# CONSTRAINT CHECKER
# ──────────────────────────────────────────────────────────────────────────────

class ConstraintChecker:
    """Validates a completed schedule against all scheduling constraints."""

    def run_all(
        self,
        sections: Dict[str, ScheduledSection],
        faculty_limits: Dict[str, int],
    ) -> bool:
        checks = [
            ("C1  Faculty course load matches limits",          self._c1_load),
            ("C2  Faculty daily span ≤ 9 h",                   self._c2_daily),
            ("C3  Faculty ≤ 2 sections of same course",         self._c3_duplicates),
            ("C4  Faculty teaches ≤ 4 days/week",               self._c4_days),
            ("C5  No blank faculty field",                      self._c5_assigned),
            ("C7  Lecture/lab duration in valid range",         self._c7_durations),
            ("C9  Lab on different day than lecture",           self._c9_lab_day),
            ("C10 Lab is exactly one day",                      self._c10_lab_one_day),
            ("C11 ≤ 10 concurrent sections per time slot",      self._c11_concurrency),
            ("C12 Graduate courses start between 5–8 PM",       self._c12_grad_time),
            ("C13 Same faculty for lecture and its lab",        self._c13_lab_faculty),
            ("C14 ≤ 2 sections of same course at same time",    self._c14_time_dupes),
            ("C15 Lecture day patterns: MW / TTh / WF only",    self._c15_patterns),
            ("C16 Sections balanced across weekdays (≤ 40 %)",  self._c16_balance),
        ]

        print("\n══════════════════ CONSTRAINT VALIDATION ══════════════════")
        all_ok = True
        for label, fn in checks:
            try:
                ok = fn(sections, faculty_limits)
            except Exception as exc:
                print(f"  ⚠  {label}: exception — {exc}")
                ok = False
            print(f"  {'✓ PASS' if ok else '✗ FAIL'}  {label}")
            all_ok = all_ok and ok
        print("════════════════════════════════════════════════════════════\n")
        return all_ok

    # ── individual checks ───────────────────────────────────────────

    def _c1_load(self, sections, limits):
        counts: Dict[str, int] = {}
        for s in sections.values():
            if not s.is_lab:
                counts[s.faculty] = counts.get(s.faculty, 0) + 1
        ok = True
        for fac, count in counts.items():
            if fac == "TBA":
                continue
            expected = limits.get(fac, 3)
            if count > expected:
                print(f"    {fac}: {count} courses (expected {expected}) — OVERLOADED")
                ok = False
            elif count < expected:
                print(f"    ⚠ {fac}: {count} courses (target {expected}) — under target")
                # Under-target is a warning only, not a failure
        return ok

    def _c2_daily(self, sections, _):
        fac_days: Dict[str, Dict[str, List[Tuple[int, int]]]] = {}
        for s in sections.values():
            if not s.start_time:
                continue
            for d in s.days:
                fac_days.setdefault(s.faculty, {}).setdefault(d, []).append(
                    (t2m(s.start_time), t2m(s.end_time))
                )
        ok = True
        for fac, days in fac_days.items():
            for d, slots in days.items():
                span_hr = (max(e for _, e in slots) - min(s for s, _ in slots)) / 60
                if span_hr > 9:
                    print(f"    {fac} on {d}: {span_hr:.1f} h span (> 9 h)")
                    ok = False
        return ok

    def _c3_duplicates(self, sections, _):
        counts: Dict[Tuple[str, str], int] = {}
        for s in sections.values():
            if not s.is_lab:
                key = (s.faculty, s.course_number)
                counts[key] = counts.get(key, 0) + 1
        ok = True
        for (fac, course), n in counts.items():
            if fac != "TBA" and n > 2:
                print(f"    {fac} teaches {n} sections of {course}")
                ok = False
        return ok

    def _c4_days(self, sections, _):
        fac_days: Dict[str, set] = {}
        for s in sections.values():
            fac_days.setdefault(s.faculty, set()).update(s.days)
        ok = True
        for fac, days in fac_days.items():
            if fac != "TBA" and len(days) >= 5:
                print(f"    {fac} teaches {len(days)} days ({','.join(sorted(days))})")
                ok = False
        return ok

    def _c5_assigned(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if not s.faculty:
                print(f"    {sid} has empty faculty field")
                ok = False
        return ok

    def _c7_durations(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if not s.start_time:
                continue
            dur = t2m(s.end_time) - t2m(s.start_time)
            if s.is_lab and not (100 <= dur <= 130):
                print(f"    LAB {sid}: {dur} min (expect 100–130)")
                ok = False
            elif not s.is_lab and not (60 <= dur <= 260):
                print(f"    LEC {sid}: {dur} min out of range")
                ok = False
        return ok

    def _c9_lab_day(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if not s.is_lab:
                continue
            base = sid.replace("-LAB", "")
            if base in sections:
                shared = set(sections[base].days) & set(s.days)
                if shared:
                    print(f"    {sid}: shares day(s) {shared} with lecture")
                    ok = False
        return ok

    def _c10_lab_one_day(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if s.is_lab and len(s.days) > 1:
                print(f"    {sid}: lab on multiple days {s.days}")
                ok = False
        return ok

    def _c11_concurrency(self, sections, _):
        day_intervals: Dict[str, List[Tuple[int, int]]] = {d: [] for d in ALL_DAYS}
        for s in sections.values():
            if not s.start_time:
                continue
            for d in s.days:
                day_intervals[d].append((t2m(s.start_time), t2m(s.end_time)))

        ok = True
        for d, intervals in day_intervals.items():
            for s1, e1 in intervals:
                concurrent = sum(1 for s2, e2 in intervals if not (e1 <= s2 or e2 <= s1))
                if concurrent > 10:
                    print(f"    {d} {s1 // 60:02d}:{s1 % 60:02d}: {concurrent} concurrent sections")
                    ok = False
                    break
        return ok

    def _c12_grad_time(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if s.start_time and is_grad(s.course_number):
                if not (GRAD_START_HR <= s.start_time.hour < GRAD_END_HR):
                    print(f"    {sid}: starts at {s.start_time} (not 5–8 PM)")
                    ok = False
        return ok

    def _c13_lab_faculty(self, sections, _):
        ok = True
        for sid, s in sections.items():
            if s.is_lab:
                base = sid.replace("-LAB", "")
                if base in sections and sections[base].faculty != s.faculty:
                    print(f"    {sid}: lab={s.faculty} ≠ lecture={sections[base].faculty}")
                    ok = False
        return ok

    def _c14_time_dupes(self, sections, _):
        seen: Dict[tuple, int] = {}
        for s in sections.values():
            if not s.start_time:
                continue
            key = (s.course_number, tuple(sorted(s.days)), t2m(s.start_time), t2m(s.end_time))
            seen[key] = seen.get(key, 0) + 1
        ok = True
        for (course, days, start, _), n in seen.items():
            if n > 2:
                print(f"    {course}: {n} sections at same time on {days} ({start // 60:02d}:{start % 60:02d})")
                ok = False
        return ok

    def _c15_patterns(self, sections, _):
        valid = [{"M", "W"}, {"T", "Th"}, {"W", "F"}]
        ok = True
        for sid, s in sections.items():
            if not s.is_lab and len(s.days) == 2 and set(s.days) not in valid:
                print(f"    {sid}: invalid 2-day pattern {s.days}")
                ok = False
        return ok

    def _c16_balance(self, sections, _):
        count = {d: 0 for d in ALL_DAYS}
        for s in sections.values():
            for d in s.days:
                count[d] += 1
        avg = sum(count.values()) / len(count)
        ok = True
        for d, c in count.items():
            if avg > 0 and abs(c - avg) > 0.4 * avg:
                print(f"    {d}: {c} sections vs avg {avg:.1f} (>40 % deviation)")
                ok = False
        return ok


# ──────────────────────────────────────────────────────────────────────────────
# EXPORTERS
# ──────────────────────────────────────────────────────────────────────────────

def export_json(
    sections: Dict[str, ScheduledSection],
    courses: List[Course],
    path: str = "schedule.json",
) -> None:
    sections_count = {normalize(c.number): c.sections for c in courses}
    events = []

    for sid, s in sections.items():
        if not s.start_time:
            continue
        total = sections_count.get(normalize(s.course_number), 1)
        parts = sid.split("-")
        sec_num = parts[1] if len(parts) >= 2 and parts[1].isdigit() else None
        display = s.course_number if total <= 1 or sec_num is None else f"{s.course_number}-{sec_num}"

        for day in s.days:
            events.append({
                "id": sid,
                "day": day,
                "course": display,
                "prof": s.faculty,
                "room": s.room,
                "start": s.start_time.strftime("%H:%M"),
                "end": s.end_time.strftime("%H:%M"),
                "isLab": s.is_lab,
            })

    with open(path, "w") as f:
        json.dump(events, f, indent=2)
    print(f"✓ Exported {len(events)} events → {path}")


def export_csv(
    sections: Dict[str, ScheduledSection],
    course_titles: Dict[str, str],
    path: str = "schedule.csv",
) -> None:
    def split_subj_crse(num: str) -> Tuple[str, str]:
        subj = re.match(r"([A-Za-z]+)", num or "")
        crse = re.search(r"(\d+)", num or "")
        return (subj.group(1) if subj else ""), (crse.group(1) if crse else "")

    def section_label(sid: str) -> str:
        parts = sid.split("-")
        if len(parts) < 2:
            return ""
        return f"{parts[1]}L" if len(parts) >= 3 and parts[2].upper() == "LAB" else parts[1]

    def fmt_time(t: time) -> str:
        h = t.hour % 12 or 12
        return f"{h:02d}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["CRN", "Subj", "Crse", "Section", "Location", "Credit",
                    "Title", "Days", "Time", "Cap.", "Act.", "Rem", "Instructor", "Date (MM/DD)"])
        for sid, s in sections.items():
            if not s.start_time:
                continue
            subj, crse = split_subj_crse(s.course_number)
            title = course_titles.get(normalize(s.course_number), "")
            if s.is_lab:
                title = f"{title} - LAB" if title else "LAB"
            w.writerow([
                "",                                         # CRN (empty)
                subj, crse,                                 # Subj / Crse
                section_label(sid),                         # Section
                s.room or "",                               # Location
                "",                                         # Credit (empty)
                title,                                      # Title
                "".join(s.days),                            # Days
                f"{fmt_time(s.start_time)}-{fmt_time(s.end_time)}",  # Time
                25, "", "",                                 # Cap / Act / Rem
                s.faculty or "",                            # Instructor
                "",                                         # Date (empty)
            ])
    print(f"✓ Exported Excel-style CSV → {path}")


# ──────────────────────────────────────────────────────────────────────────────
# REPORTING
# ──────────────────────────────────────────────────────────────────────────────

def print_summary(
    sections: Dict[str, ScheduledSection],
    courses: List[Course],
    faculty_limits: Dict[str, int],
    slot_load: Dict[str, Dict[str, int]],
) -> None:
    total_lec = sum(1 for s in sections.values() if not s.is_lab)
    total_lab = sum(1 for s in sections.values() if s.is_lab)
    total_fac = len({s.faculty for s in sections.values() if s.faculty and s.faculty != "TBA"})
    am = sum(1 for s in sections.values() if s.start_time and s.start_time.hour < AM_CUTOFF_HR)
    pm_count = sum(1 for s in sections.values() if s.start_time and s.start_time.hour >= AM_CUTOFF_HR)

    print("\n══════════════════ GLOBAL SUMMARY ══════════════════")
    print(f"  Courses in course_list.csv  : {len({c.number for c in courses})}")
    print(f"  Lecture sections            : {total_lec}")
    print(f"  Lab sections                : {total_lab}")
    print(f"  Unique faculty (≠ TBA)      : {total_fac}")
    print(f"  AM / PM sections            : {am} / {pm_count}")
    print("════════════════════════════════════════════════════\n")

    print("══════════════ COURSE SECTION COUNTS ══════════════")
    counts: Dict[str, Dict[str, int]] = {}
    for s in sections.values():
        e = counts.setdefault(s.course_number, {"lec": 0, "lab": 0})
        e["lab" if s.is_lab else "lec"] += 1
    for course in sorted(counts):
        e = counts[course]
        lab_str = f"{e['lab']} lab(s)" if e["lab"] else "no labs"
        print(f"  {course}: {e['lec']} lecture(s), {lab_str}")
    print("════════════════════════════════════════════════════\n")

    grad = {sid: s for sid, s in sections.items() if is_grad(s.course_number) and s.start_time}
    if grad:
        print("══════════════ GRADUATE (5000+) TIMINGS ════════════")
        by_course: Dict[str, list] = {}
        for sid, s in grad.items():
            by_course.setdefault(s.course_number, []).append((sid, s))
        for course in sorted(by_course):
            print(f"  {course}:")
            for sid, s in sorted(by_course[course], key=lambda x: x[1].start_time):
                label = "LAB" if s.is_lab else "LEC"
                print(f"    {sid} [{label}] {''.join(s.days)} {s.start_time.strftime('%H:%M')}-{s.end_time.strftime('%H:%M')} | {s.faculty} | {s.room}")
        print("════════════════════════════════════════════════════\n")

    tba = [(sid, s) for sid, s in sections.items() if s.faculty == "TBA"]
    if tba:
        print("══════════════ TBA FACULTY SECTIONS ════════════════")
        for sid, s in sorted(tba):
            label = "LAB" if s.is_lab else "LEC"
            print(f"  {sid}: {s.course_number} [{label}] {''.join(s.days) or '-'} | {s.room}")
        print("════════════════════════════════════════════════════\n")

    print("══════════════ FACULTY ASSIGNMENTS ═════════════════")
    fac_map: Dict[str, list] = {}
    for sid, s in sections.items():
        fac_map.setdefault(s.faculty, []).append((sid, s))
    for fac in sorted(fac_map):
        sec_list = fac_map[fac]
        lec_count = sum(1 for _, s in sec_list if not s.is_lab)
        target = faculty_limits.get(fac)
        tgt_str = f", target={target}" if target is not None and fac != "TBA" else ""
        print(f"\n  {fac}: {len(sec_list)} section(s) [lec={lec_count}{tgt_str}]")
        for sid, s in sorted(sec_list, key=lambda x: (x[1].course_number, x[0])):
            label = "LAB" if s.is_lab else "LEC"
            days = "".join(s.days) if s.days else "-"
            start = s.start_time.strftime("%H:%M") if s.start_time else "-"
            end = s.end_time.strftime("%H:%M") if s.end_time else "-"
            print(f"    {sid} [{label}] {days} {start}-{end} | {s.room}")
    print("\n════════════════════════════════════════════════════\n")

    print("══════════════ SLOT UTILIZATION BY DAY ═════════════")
    for day in ALL_DAYS:
        slots = slot_load.get(day, {})
        if slots:
            print(f"  {day}:")
            for k, v in sorted(slots.items()):
                print(f"    {k}: {v} section(s)")
    print("════════════════════════════════════════════════════\n")


# ──────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ──────────────────────────────────────────────────────────────────────────────

def _run() -> None:
    courses        = load_courses("data/course_list.csv")
    fac_prefs      = load_faculty_preferences("data/prof_preferences.csv")
    timeslots      = load_timeslots("data/timings.csv")
    faculty_limits = load_faculty_loads("data/faculty_load.csv")
    rooms          = load_rooms("data/rooms.csv")
    room_prefs     = load_room_preferences("data/room_preferences.csv")

    course_titles = {normalize(c.number): c.name for c in courses}
    sections      = build_sections(courses, fac_prefs)

    # Faculty, time, and room are now assigned jointly inside build_schedule.
    time_sched    = TimeSlotScheduler(timeslots)
    room_assigner = RoomAssigner(rooms, room_prefs)
    scheduled     = build_schedule(sections, fac_prefs, faculty_limits, time_sched, room_assigner)

    print_summary(scheduled, courses, faculty_limits, time_sched.slot_load)
    ConstraintChecker().run_all(scheduled, faculty_limits)
    export_json(scheduled, courses)
    export_csv(scheduled, course_titles)


class _Tee(io.TextIOBase):
    """Write to multiple streams simultaneously."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s: str) -> int:
        for st in self.streams:
            if not getattr(st, "closed", False):
                try:
                    st.write(s)
                    st.flush()
                except ValueError:
                    pass
        return len(s)

    def flush(self) -> None:
        for st in self.streams:
            if not getattr(st, "closed", False):
                try:
                    st.flush()
                except ValueError:
                    pass


def main() -> None:
    with open("result.txt", "w", encoding="utf-8") as log:
        tee = _Tee(sys.stdout, log)
        with contextlib.redirect_stdout(tee):
            _run()


if __name__ == "__main__":
    main()
