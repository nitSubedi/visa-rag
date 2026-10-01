# Visa Research — beta for Mac

A research tool for U.S. student-visa questions (F-1, OPT, STEM OPT, H-1B cap-gap,
priority dates). It answers from the regulations (8 CFR), the statute (INA) and the
USCIS Policy Manual, and shows the exact passages it relied on. It runs entirely on
your Mac: your questions never leave it.

**This is a beta and it is research, not legal advice.** Rules marked "decided by the
app" are computed from the quoted law; the written answer can be wrong or miss
something. Confirm anything that affects your status with your DSO (free) or an
immigration attorney.

## What you need

- A Mac with Apple Silicon (M1 or newer) and macOS 12 or later
- About 4 GB of free space
- No internet is required; when online it checks once a day for a newer Visa Bulletin
  table and court-injunction list

## Install

1. Open `Visa-Research-0.1.0-macos-arm64.dmg` and drag **Visa Research** into
   **Applications**.
2. Open it from Applications. macOS will say it can't verify the developer — the app
   is not signed with a paid Apple developer account.
3. Open **System Settings → Privacy & Security**, scroll down to the message about
   "Visa Research", click **Open Anyway**, and confirm. (On macOS 14 or older you can
   instead right-click the app and choose **Open**.) You only do this once.

## First launch

- **Starting up** (about half a minute): the app loads its model. No internet needed.
- **Build the library** (one time, about seven minutes, no internet needed): it indexes
  the regulations, the statute and the Policy Manual that come with the app. Your fan
  may run while it does.
- Then ask anything. If a rule needs a fact about you (for example how many months of
  full-time CPT you did), the app asks before answering.

## What it sends over the internet

Only a once-a-day check for a newer Visa Bulletin table and court-injunction list
(public data). Nothing you type, and nothing
from your profile, is ever sent.
