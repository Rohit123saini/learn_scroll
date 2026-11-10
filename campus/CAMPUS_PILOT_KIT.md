# LearnScroll Campus — School Pilot Kit

A hand-over guide for the person who runs a school pilot (principal, coordinator or the first teacher).
It matches the **Pilot checklist** card at the top of *Campus → Setup*, step for step.
Target: a real class using the app every day within **one week**.

---

## 1. Before you start (10 minutes)

| You need | Why |
|---|---|
| A LearnScroll account for the campus **admin** (principal / coordinator) | Admin = full set-up rights for this campus |
| School name, type (school / college / coaching) | Used when you create the campus |
| Class list (e.g. Class 9–12) and section names (A, B…) | Students join a *section* |
| Subject list and the teachers who teach them | Timetable, attendance and results hang off these |
| A WhatsApp group (or notice board) per class | This is how invite codes travel |

**Roles at a glance**

| Role | Can do |
|---|---|
| Admin / Principal-HOD | Everything for the campus: structure, staff, invite codes, fees, results |
| Class teacher | Attendance and notices for their own section; can create that section's invite code |
| Subject teacher | Their own approved section + subject only |
| Student | Join with code, see timetable, notices, assignments, results |
| Parent | Link to a child using the parent invite link |

---

## 2. The seven steps (the in-app checklist)

1. **Campus verified** — create the campus (*Campus → +*). A LearnScroll admin approves it, usually within a working day. Until then you can set everything up, but you **cannot invite students or staff**. If it stays pending, contact the pilot manager.
2. **Academic session set as current** — *Setup → Sessions*: add e.g. `2026-27` with start/end dates and mark it **current**. Exactly one session can be current.
3. **Classes & sections created** — *Setup → Classes*, open a class, add its sections. Optional: add departments first if you are a college.
4. **Subjects added** — *Setup → Subjects*. Add rooms too if you plan a timetable.
5. **Teachers invited** — *Setup → Staff*. Give each person a role. Subject teachers need an approval from admin/principal before they can manage a subject.
6. **Invite code shared** — open a section → **Invite classmates**. Tap **Share invite**; the app writes the message for you. Post it in the class WhatsApp group.
7. **10 students on board** — the pilot counts as working once about ten real students have joined. (Operators can change the target with `CAMPUS_PILOT_TARGET_STUDENTS`.)

The card shows the first unfinished step with a one-line hint. Pull down on the Setup screen to refresh it.

---

## 3. How students join (invite codes)

**Teacher side** (Section → *Invite classmates*)

- **Share invite** opens the phone's share sheet (WhatsApp, Telegram, SMS…) with the message ready.
- **Copy message** copies the same text.
- The screen tells you how many have joined, how many **seats are left** (if you set a limit) and when the code **expires**.
- **Get new code** retires the old code immediately. Students who already joined stay enrolled.
- **Deactivate** stops new joins without creating a new code.
- Codes are *not* single-use: the whole class can use the same one, until you deactivate it, it expires, or it hits its limit.

**Student side** (Campus → *Join with code*)

- Paste the code — or paste the **whole forwarded WhatsApp message**; the app finds the code in it. Spaces, dashes and lower case are fine.
- On success the app says which class they joined (e.g. *Class 10 - A at Green Valley School*).
- While the founding window is open (see §5) the first students also get a **Founding Member** badge.

**What the student sees when it does not work**

| Message | Meaning | Fix |
|---|---|---|
| Invalid invite code | Typo, or a code from another school | Check the code |
| This invite code was deactivated | Teacher turned it off or rotated it | Ask for the new code |
| This invite code has expired | Past its expiry date | Teacher taps *Get new code* |
| This invite code has reached its limit | Seat limit used up | Teacher raises the limit by creating a new code |
| Campus is pending platform verification | Step 1 is not finished | Wait for approval |

---

## 4. Week-one plan

| Day | Who | What |
|---|---|---|
| 1 | Admin | Steps 1–4 (verify, session, classes, subjects) |
| 2 | Admin | Invite teachers; approve subject-teacher requests |
| 3 | Class teachers | Share the invite code in each class group; ask students to join |
| 4 | Class teachers | Post the first notice; mark first attendance |
| 5 | Subject teachers | Create the first assignment; schedule one live class |
| 7 | Admin | Check the pilot checklist is at 100 % and note blockers |

**Message templates**

> *Class group*: "Join our class on LearnScroll! Open the app → Campus → Join with code and paste the message below. Notices, timetable and assignments will be here from now on."
> *(then forward the message the app generated)*

> *Parents*: "We use LearnScroll for class notices and attendance. Please link your child using the parent invite link we send you."

---

## 5. Rewards that cost nothing (pilot incentives)

Nothing here moves coins; it is recognition and small perks.

- **Founding Member** badge — for students who join through a campus invite code, for friends who redeem a referral, and for anyone who refers three friends, **while the founding window is open** (ends at `FOUNDING_MEMBER_UNTIL` or after `FOUNDING_MEMBER_MAX_AWARDS` badges).
- **Streak freeze** — each redeemed referral gives **both** people one streak freeze (a free "skip a day"), up to the usual holding limit.
- **Connector** badge for the first friend referred, **Community Builder** for five.

---

## 6. Reminders & quiet hours

Students and teachers control this in *Notifications → Preferences*.

- **Class reminders** — a push a few minutes before a live class or school period (campus default: 5 minutes; a school can switch class reminders off for the whole campus).
- **Streak reminder** — one evening nudge per day (from 22:00 by default), only if the streak is at risk. Sent at most once a day; people who already checked in are skipped.
- **Quiet hours** (e.g. 22:00–07:00, in the person's own timezone) and **Pause all alerts** (1 h / 8 h / 24 h): no push during these windows. The notification still appears in the in-app list. A streak reminder that was held back is delivered as soon as quiet hours end, if the day has not rolled over yet.
- Muting a type silences it on every channel.

Tip for schools: ask staff to leave class reminders **on** and not to set quiet hours across school hours.

---

## 7. Troubleshooting

| Problem | Check |
|---|---|
| "Not permitted" on Setup | Only admin / principal-HOD can set up the campus |
| Students cannot join | Campus approved? Code active, not expired, not full? Copy-pasted correctly? |
| Student joined the wrong section | Ask the admin to correct the enrolment before attendance starts |
| No reminder arrived | Is push on? Is the type muted? Quiet hours/pause active? Is the campus's class-reminder setting on? |
| Checklist stuck at "Campus verified" | Waiting for platform approval |

---

## 8. For the pilot manager (operator settings)

| Setting (env) | Default | Meaning |
|---|---|---|
| `CAMPUS_PILOT_TARGET_STUDENTS` | 10 | Students needed for the last checklist step |
| `FOUNDING_MEMBER_UNTIL` | *(none)* | Last day (ISO date) for founding badges |
| `FOUNDING_MEMBER_MAX_AWARDS` | 1000 | Maximum founding badges handed out |
| `FOUNDING_MEMBER_REFERRALS_NEEDED` | 3 | Referrals that make a referrer a founding member |
| `REFERRAL_REWARD_FREEZE_REFERRER` / `_REFEREE` | 1 / 1 | Streak freezes per redeemed referral |
| `STREAK_REMINDER_HOUR` | 22 | Hour (server time) the streak reminder window opens |

API for dashboards: `GET /campus/<campus_id>/pilot-kit/` (admin only) returns the same checklist the app shows.
