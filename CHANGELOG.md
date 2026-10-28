v0.5 - Iteration 3 - [In progress, add the completed date at release]
- Group invitations: group creators invite students by UTA email; students accept or decline from the Invitations page
- Members & Invitations page for creators: current members, pending invitations, cancel an invitation, past invitations
- Invitations expire after 7 days, or when the group is cancelled or has finished meeting; full groups cannot take new invitations or accepts
- Invitation activity creates notifications for the invited student (new invitation) and the inviter (accepted or declined)
- UTA email verification: new accounts confirm a 6-digit code sent to their @mavs.uta.edu address before they can log in (printed in the terminal in demo mode, real email with the STUDYMORE_SMTP_* settings)
- Edit group details: creators can change a group's name, description, meeting type, date, time, location, link, and size
- Group pages list members with an avatar and, if the member allows it, class year, major, and study style
- Profile: avatar color and a privacy setting for what group members can see
- Removed stray patch, backup, and .bak files from the repository

v0.4 - Iteration 2 polish - [Completed 10/18/2026]
- New color theme with hover effects and status colors across all pages
- Profile tab: students can add their major, courses, study style, and what they can help with
- My Groups filters: status, meeting type, course, and groups you created
- "You created this" badge on My Groups

v0.3 - Iteration 2 - [Completed 10/10/2026]
- Group status management: creators can Mark Completed, Cancel Group, or Reopen Group
- Groups show as Completed automatically once the meeting time has passed
- Only the group creator can change a group's status
- Study requests and smart study matching
- Weekly availability grid and group availability heatmap
- Demo data seeder (seed_demo_data.py)
- Tests for search, group status, and status routes

v0.2 - Iteration 1 - [Completed 09/2026]
- Create, search, join, and leave study groups
- Accounts: register and log in
- Required fields marked with an asterisk on the Create Group form

v0.1 - inception phase setup - [Completed 09/06/2026]
