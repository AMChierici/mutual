# Getting Started

A non-technical walkthrough for pool organizers. If you're a developer, see [`architecture.md`](architecture.md) and [`../CONTRIBUTING.md`](../CONTRIBUTING.md) instead.

## Before you install anything

Have these conversations with your group first. Software does not fix unclear agreements.

1. **Who is in?** Names, contact, what role each person plays.
2. **Where does the money actually live?** A joint bank account is simplest. Whoever has access is the treasurer.
3. **What do you want to cover?** Look at [`policies/`](../policies/) for templates. Pick one and edit it together. Print the final version and put it on a fridge.
4. **How do you decide?** Unanimous? Majority? A jury of three? Read [`governance/`](../governance/) and pick.
5. **What's the failsafe?** What happens if the pool runs dry? If two people leave at once? If someone files a claim everyone thinks is unreasonable? Write the answers down *before* it happens.

## Install

You need a computer that's on most of the time. A Raspberry Pi, an old laptop, or a cheap VPS works.

```bash
git clone https://github.com/YOU/mutual
cd mutual
docker compose up -d
```

Open `http://localhost:8000` (or whatever IP your server has on your network).

## First-run setup

The app walks you through:

1. Creating the pool
2. Adding members (each gets a private link to log in — no passwords, magic-link only)
3. Picking a policy template and editing it inline
4. Picking governance rules
5. Recording the starting balance

## Day to day

- Treasurer logs each contribution as it lands in the bank account
- Anyone can submit a claim with photos and a description
- Voting happens in the app (or in person, then logged)
- Approved claims show as "ready to pay" — treasurer transfers the money and marks paid
- Pool dashboard shows current balance, claim history, premium adequacy

## Every six months

- Look at the dashboard's "model output" tab
- Discuss as a group whether premiums need adjusting
- Update the policy template if you've hit edge cases — log them in the policy file
- Commit the policy file to your private fork so you have history

## When something feels weird

Ask in the GitHub Discussions. Most "weird" things are governance issues that other pools have already hit.
