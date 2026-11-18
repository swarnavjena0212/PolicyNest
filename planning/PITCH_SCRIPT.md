# PolicyNest: auction-day pitch (no Q&A)

**Length:** about 5 minutes. Three speakers: **A** (story), **B** (demo), **C** (proof and close). Swap in your real names.
**Screen:** the demo site, https://policynest-demo.embarko.app/?demo=1, opened and signed in *before* you walk on.
**Rule for the whole pitch:** every number we say is real and comes from our own tests. Anything simulated, we call simulated.

---

## 0. Before you walk on (setup, 2 min backstage)

- Laptop on the Application page, the "Before you start" checklist showing.
- Sound **on** (the voice guide speaks). Language set to **English** first.
- Demo controls panel open, with "Load sample applicant" and "Use specimen" ready.
- **Backup:** a 60-second screen recording of the same demo on the desktop. If the Wi-Fi drops, B says "Let me show you the one we recorded this morning" and plays it. Never debug on stage.

---

## 1. The hook — 0:00 to 0:45 (A)

> *(Walk to centre. Silence for two seconds. Then:)*
>
> "Meet Ramesh. He's 34. He runs a small shop in Indore, and he wants to earn more by selling insurance to his neighbours.
>
> India needs people like Ramesh. Insurance companies sign up agents like him by the thousands. They're called POSPs.
>
> So Ramesh does everything right. He uploads his PAN card, his Aadhaar, his marksheet and his bank details.
>
> And then… nothing. A call on day three: 'Your marksheet is blurry.' Another call on day five: 'Your bank name doesn't match.' One problem at a time, one phone call at a time.
>
> In the challenge's own data, that wait comes to more than **eight days**. Many people like Ramesh simply give up.
>
> *(Pause.)*
>
> We asked one question: **what if Ramesh knew everything in eight seconds instead of eight days?**"

*(Ramesh is an illustrative person. The 8.4-day median comes from the PS-01 dataset in our simulation.)*

---

## 2. The magic moment — 0:45 to 2:30 (B, live)

> **B:** "This is PolicyNest. I'm going to be Ramesh right now. Watch the screen."

**Beat 1: the timer starts.** Press **Begin**. The circular timer ring starts draining.
> "From the moment he starts, Ramesh has one focused 30-minute sitting. It's not an exam: he can take more time, and nothing is ever lost."

**Beat 2: the AI refuses the wrong document (the first "oh").** Drop an **Aadhaar** specimen into the **PAN** slot.
> "Ramesh is tired, and he uploads the wrong card. Every other system would accept it and find out a week later."

*(The screen refuses it: that's an Aadhaar, not a PAN.)*
> "PolicyNest read it, on his own phone, and told him in one second."

**Beat 3: instant feedback.** Upload the right specimens one by one, or press "spec-all". The cheer toasts appear with his first name.
> "Each document is read and checked the moment it lands. And it speaks to him by name."

**Beat 4: THE WOW MOMENT.** Switch one specimen to the **"DOB off by a year"** variant, then press **Submit**. Wait about 8 seconds. *Stay silent while it runs and let the room watch.*

When the result appears:
> "About **25 rulebook checks**, every document checked against every other, anchored on the PAN card. And instead of five phone calls over eight days…"

*(Point at the one consolidated message.)*
> "**…one message, with everything he needs to fix, at once.**"

Switch the language to **Hindi** and turn on the **voice guide**. Let it speak one line in Hindi.
> "In his language, in his own voice-guided flow. For Ramesh, that's the difference between giving up and getting started."

---

## 3. Why you can trust it — 2:30 to 3:45 (C)

> "Demos are easy. So here's what's under it, and it's all live today."

Say it as four short punches, one breath each:

1. **"It's honest."** "The AI never approves or rejects a hard case on its own. A surname change, a weak face match, an unknown school board: those go to a human reviewer, with the evidence side by side."
2. **"It's private."** "Document photos never leave the applicant's phone. Our server never stores a full PAN, Aadhaar or account number."
3. **"It's tested."** "We ran the engine on all **600 cases** in the challenge's test set. It got 98% on the first run, and 100% after rulebook fixes. We tell you that's in-sample. All ten of the brief's test scenarios pass."
4. **"It has a co-pilot."** "An AI assistant that knows exactly where Ramesh is stuck. It answers from the rulebook with sources, and it refuses to ask for ID numbers or predict outcomes. It scored 45 out of 45 on our guardrail tests."

---

## 4. The team, and the auction close — 3:45 to 5:00 (A, then all three)

> **A:** "Here's what we want you to remember about *us*.
>
> We got this problem with no product, no code and no data pipeline. In **five days**, we shipped:
> - a live, public portal you can open on your phone tonight;
> - an applicant side, a reviewer side and an admin side;
> - an AI assistant and a voice guide;
> - and a test suite that checks every promise we just made."

> **B:** "We don't stop at the demo. We ship."

> **C:** "We don't hide numbers. We test them."

> **A:** *(Glance at the timer still running on screen.)* "Look at the clock. Ramesh's sitting still has more than **25 minutes left**. In the time we've been talking, he went from a week of waiting to knowing exactly what to do.
>
> That's what we build. **Bid on the team that ships.**"

*(Stop. Don't say thank you first. Hold two seconds of silence, then "Thank you.")*

---

## Delivery notes

- **The magic moment is the silence during the 8-second check.** Don't fill it. The room leans in.
- **Practise the hand-offs:** A → B ("Watch the screen"), B → C ("…getting started"), C → A ("…guardrail tests").
- **Never say:** "fraud", "it approves people automatically", or any accuracy figure we didn't measure.
- **If something breaks,** go to the backup video at once and keep the script the same.
- **Run it three times tonight with a stopwatch.** If you go over 5:00, cut point 4 of section 3 first.
