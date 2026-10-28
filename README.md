# StudyMore

StudyMore helps college students find study partners for a specific course. Students can create or join study groups, post a study request when no group exists, and compare weekly availability to find the best meeting time.

## Features

- Create, search, join, and leave study groups
- Group status management: creators can mark a group Completed or Cancelled, or reopen it. A group whose meeting time has passed shows as Completed automatically.
- Study requests with smart matching by course, study goal, meeting preference, section, and overlapping availability
- Weekly availability grid and a group availability heatmap
- Accounts: register with a UTA student email (`@mavs.uta.edu`) and confirm it with a 6-digit code before logging in
- Group invitations: creators invite students by email, students accept or decline, creators manage members and pending invitations
- Edit group details, member lists with optional profile info, and a profile with avatar color and privacy setting
- Required fields are marked with an asterisk on the Create Group form

## Run it

1. Clone the repository: `git clone https://github.com/buildwithsalem/StudyMore.git`
2. Install Flask: `pip install flask`
3. From the `src` folder, run: `python app.py`
4. Open http://127.0.0.1:5000 (or the forwarded URL in GitHub Codespaces)
5. Register a new account, or load sample data first with `python seed_demo_data.py` and log in as `demo@example.com` with the password `test123`

### Email verification codes

By default (demo mode) the 6-digit code is printed in the terminal where `python app.py` is running. To send real email, set these before starting the app: `STUDYMORE_SMTP_HOST`, `STUDYMORE_SMTP_PORT` (default 587), `STUDYMORE_SMTP_USER`, `STUDYMORE_SMTP_PASSWORD`, and `STUDYMORE_MAIL_FROM`. Accounts and demo users that existed before this feature are already verified.

## Run the tests

From the `src` folder: `python -m unittest discover`

## Repository layout

- `src` - application code and tests
- `design` - screen designs and drawings (see `design/links.md`)
- `presentations` - iteration presentations
- `reviews` - reviews
- `written_deliverables` - written deliverables
- `CHANGELOG.md` - version history
- `Competitors` - competitor notes

## Team

Raghad, Michael, Salem, Muhammed