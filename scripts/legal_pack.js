// Builds the legal review pack: every message the bot can send, plus what constrains the ones it
// writes itself. Regenerate with `node scripts/legal_pack.js` after any change to templates.py.
const fs = require("fs");
const {
  AlignmentType, BorderStyle, Document, Footer, HeadingLevel, LevelFormat, PageBreak,
  Packer, Paragraph, ShadingType, Table, TableCell, TableRow, TextRun, WidthType,
} = require("docx");

const NAVY = "1F3355";
const GREY = "5A6470";
const RULE = "C9D0D8";
const W = 9360; // 6.5" content width at DXA

const p = (text, o = {}) => new Paragraph({ spacing: { after: o.after ?? 120 }, ...o, children: [new TextRun({ text, ...o.run })] });
const h1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, spacing: { before: 360, after: 160 }, children: [new TextRun({ text, color: NAVY, bold: true, size: 30 })] });
const h2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, spacing: { before: 260, after: 100 }, children: [new TextRun({ text, color: NAVY, bold: true, size: 24 })] });
const note = (text) => new Paragraph({ spacing: { after: 140 }, children: [new TextRun({ text, italics: true, color: GREY, size: 19 })] });
const bullet = (text) => new Paragraph({ numbering: { reference: "dots", level: 0 }, spacing: { after: 80 }, children: [new TextRun({ text, size: 21 })] });
const numbered = (text) => new Paragraph({ numbering: { reference: "qs", level: 0 }, spacing: { after: 140 }, children: [new TextRun({ text, size: 21 })] });

// A quoted message: indented, bordered on the left, monospaced-ish so counsel can see it is verbatim.
const quote = (lines) =>
  lines.map((line, i) =>
    new Paragraph({
      spacing: { after: i === lines.length - 1 ? 200 : 60 },
      indent: { left: 360 },
      border: { left: { style: BorderStyle.SINGLE, size: 12, color: NAVY, space: 12 } },
      children: [new TextRun({ text: line || " ", size: 21, font: "Georgia" })],
    })
  );

const cell = (children, { width, head = false, shade } = {}) =>
  new TableCell({
    width: { size: width, type: WidthType.DXA },
    shading: shade ? { type: ShadingType.CLEAR, fill: shade, color: "auto" } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: children.map((t) =>
      new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: t, bold: head, size: 19, color: head ? "FFFFFF" : "000000" })] })
    ),
  });

const table = (headers, rows, widths) =>
  new Table({
    columnWidths: widths,
    width: { size: W, type: WidthType.DXA },
    rows: [
      new TableRow({ tableHeader: true, children: headers.map((t, i) => cell([t], { width: widths[i], head: true, shade: NAVY })) }),
      ...rows.map((r, ri) =>
        new TableRow({ children: r.map((t, i) => cell([t], { width: widths[i], shade: ri % 2 ? "F4F6F8" : undefined })) })
      ),
    ],
  });

// ---------------------------------------------------------------- the messages

const D = "[DEALERSHIP]";
const L = "[NUMBER]";
const A = "[BUYER NAME]";

const body = [
  new Paragraph({ spacing: { after: 0 }, children: [new TextRun({ text: "For legal review", color: GREY, size: 20, allCaps: true, bold: true })] }),
  new Paragraph({ spacing: { before: 80, after: 60 }, children: [new TextRun({ text: "Automated Seller Engagement", color: NAVY, bold: true, size: 44 })] }),
  new Paragraph({ spacing: { after: 240 }, children: [new TextRun({ text: "Every message the system can send to a private seller", color: GREY, size: 26 })] }),
  new Paragraph({
    spacing: { after: 300 },
    border: { top: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 8 }, bottom: { style: BorderStyle.SINGLE, size: 6, color: RULE, space: 8 } },
    children: [new TextRun({ text: "Prepared 18 September 2026  ·  Draft 1.0  ·  Jurisdiction: Victoria  ·  Not legal advice", size: 19, color: GREY })],
  }),

  h1("1.  What this is, and what we are asking"),
  p("A dealership is putting an automated assistant in front of private sellers advertising cars on Facebook Marketplace. The assistant opens the conversation, asks a set series of questions about the vehicle, and — within limits a person sets in advance — makes a firm offer to buy. When a seller accepts, a human takes over.", { run: { size: 21 } }),
  p("This document contains every message the system is capable of sending. There are no others. We are asking counsel to confirm the wording is compliant before the system speaks to a member of the public.", { run: { size: 21 } }),
  h2("The questions"),
  numbered("Is the disclosure at §3.1 sufficient, in form and in placement, for the Motor Car Traders Act 1986 (Vic) and for s18 of the Australian Consumer Law?"),
  numbered("Does the offer wording at §3.4 create a binding offer, and is \"firm offer … subject to inspection\" an accurate description of what the dealership is doing?"),
  numbered("Is the 48-hour expiry at §3.4 defensible? It is genuinely enforced — the figure is withdrawn and cannot be reinstated without a person re-approving it — but we want the claim tested against s29(1)(i) ACL."),
  numbered("Does asking a seller for their mobile number during a Messenger conversation, and then texting them, establish consent under the Spam Act 2003 (Cth)? §3.6 sets out what the first text says."),
  numbered("The system asks for rego or VIN and runs a PPSR check. No privacy collection notice is currently sent. See §6.1 — we believe this is a gap."),
  numbered("§4 describes messages written by a language model rather than fixed in advance, and the controls on them. Is that control set adequate, and is there wording counsel would add to the prohibited list?"),

  new Paragraph({ children: [new PageBreak()] }),

  h1("2.  How a conversation runs"),
  p("Enough to read the messages in context. The sequence is fixed in code; the assistant cannot skip a step or invent one.", { run: { size: 21 } }),
  bullet("A lead arrives from a sourcing platform. The seller is already in the dealership's Facebook Page inbox, so the first message lands in an existing thread."),
  bullet("The assistant introduces itself, discloses that it is automated, and begins asking about the car — ten required facts: variant, odometer, service history, panel and paint, mechanical faults, tyres, keys, registration status, rego or VIN, and photographs."),
  bullet("A registration and PPSR check runs. A written-off or stolen vehicle ends the conversation before any offer."),
  bullet("A valuation is computed in code. The language model never sees a price and never calculates one."),
  bullet("An offer is presented — at first only by a person, later, once the dealership is satisfied with the wording, automatically."),
  bullet("The assistant may concede twice, to figures authorised in advance. Anything beyond that is a human decision."),
  bullet("On acceptance, a person takes over. The assistant does not arrange payment or sign anything."),
  note("Eight circumstances stop the assistant and hand to a person immediately, including any mention of a deceased estate, a seller who is not the owner, financial distress, legal threats, or a request to speak to a human."),

  h1("3.  Fixed messages"),
  p("Reproduced verbatim. Square brackets are filled from configuration at send time; nothing else varies.", { run: { size: 21 } }),

  h2("3.1  Disclosure — first message of every conversation"),
  note("Sent as part of the opening message, before any question is asked."),
  ...quote([`Hi [FIRST NAME], ${A} here from ${D}. Quick note before we start: this is an automated assistant from ${D} (LMCT ${L}). A person from our team is available any time — just ask.`, "", "We're interested in your [YEAR MAKE MODEL]. [FIRST QUESTION]"]),

  h2("3.2  Identifying itself when challenged"),
  note("Sent whenever the seller asks whether they are talking to a person, in any form of words. The conversation then goes to a human and the assistant stops."),
  ...quote([`Straight answer: you're talking to an automated assistant run by ${D}. I'll hand you over — a person from our team will pick this up from here.`]),

  h2("3.3  During discovery"),
  note("One question at a time. If an answer contradicts the listing or a check, the assistant raises it rather than choosing a side."),
  ...quote(["One thing to check: the listing said [FIELD] [CLAIMED], but [the PPSR check / the VIN decode / the rego check / the photos] shows [ACTUAL]. Which is right?"]),
  ...quote(["Got [N] — could you send the rest? I need [M] in total: the six exterior angles, the interior, and the dash with the ignition on."]),
  ...quote(["Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly."]),

  h2("3.4  The offer"),
  note("Two modes. At launch — and for as long as the dealership wants — a valuation is computed but a person decides whether and at what figure to present it. The assistant says only this, and then stops:"),
  ...quote([`Thanks — I've got everything. ${A} is working out a firm number and will send it through shortly.`]),
  note("Once the dealership switches automatic presentation on, the assistant presents the opening figure itself. The figure is computed in code from the valuation and the margin the dealership sets. The expiry is real: when it passes, the offer is marked lapsed in the database and the assistant cannot present that figure again."),
  ...quote([`Here's where we've landed: $[AMOUNT] for the [VEHICLE], subject to inspection. That's a firm offer and it's open until [TIME] on [DAY DATE] — after that the valuation inputs move and it lapses.`, "", `If it works, reply yes and ${A} will book the inspection and sort payment. If not, no pressure — just let me know.`]),

  h2("3.5  Negotiation and its limits"),
  note("Two concessions are authorised in advance. The wording deliberately does not claim either is the last — see §5.2."),
  ...quote([`I can go to $[AMOUNT] — same terms, subject to inspection, open until [TIME] on [DAY DATE].`]),
  note("When the authorised concessions are exhausted:"),
  ...quote([`$[AMOUNT] is as far as I can take it on my own. Going further isn't my call, so ${A} will have a look and come back to you.`]),
  note("When the 48 hours pass without a reply. Carries no figure — restating a withdrawn number would undo the withdrawal:"),
  ...quote([`The 48 hours on that offer are up, so it's lapsed. If the car's still available and you'd like another look at it, say the word and ${A} will re-run the numbers.`]),

  h2("3.6  Moving to text message"),
  note("Facebook closes the messaging window 24 hours after a seller's last reply. If the seller has given a mobile number, the conversation continues by SMS. This text is prefixed to the first message sent on the new channel."),
  ...quote([`${D} (LMCT ${L}) here, carrying on our chat from Marketplace. Reply STOP any time and I'll leave you alone.`]),
  note("On receiving STOP, in any capitalisation, the assistant sends one acknowledgement and never messages again:"),
  ...quote(["Understood — I won't message again."]),

  h2("3.7  Following up on silence"),
  note("At most three follow-ups across a conversation, then it closes itself. No new information, no deadline that was not already given."),
  ...quote(["No rush at all — just checking you saw the question about [TOPIC]. If you'd rather leave it here, that's completely fine, just say so."]),
  ...quote([`Just checking in — that offer still stands. If you'd like to go ahead, say the word and ${A} will sort the inspection. If not, no hard feelings.`]),
  ...quote(["I'll leave it there so I'm not clogging up your messages. If you change your mind about selling, reply any time and we'll pick it back up."]),

  h2("3.8  Closing"),
  ...quote([`Great — ${A} will be in touch to book the inspection and confirm the details. Thanks [FIRST NAME].`]),
  ...quote(["No problem — thanks for your time. If anything changes before [TIME] on [DAY DATE], the offer stands until then."]),

  new Paragraph({ children: [new PageBreak()] }),

  h1("4.  Messages the assistant writes itself"),
  p("Questions during discovery, and the sentence that carries an offer, are written by a language model rather than fixed in advance, so that a seller who has already answered something is not asked again in the same words. What the model may say is constrained in code, and every draft is checked before it is sent. A draft that fails the check is rewritten once; if it fails again, the fixed wording at §3 goes out instead.", { run: { size: 21 } }),
  p("A message is refused if it:", { run: { size: 21 } }),
  bullet("Contains any dollar figure at all before a valuation exists."),
  bullet("Contains any dollar figure other than the exact one being presented."),
  bullet("Promises to buy, guarantees anything, or describes the deal as done — the assistant has no authority to bind the dealership."),
  bullet("Mentions other buyers, competing offers, other dealers, or anyone else being interested."),
  bullet("Uses urgency language: act now, last chance, today only, limited time, don't miss out, now or never."),
  bullet("States a year, an odometer reading, or a vehicle make that is not in the recorded facts for that car."),
  bullet("Omits the offer amount or the expiry, word for word, when presenting an offer."),
  bullet("Exceeds the character limit of the channel it is being sent on."),
  bullet("Shouts, in capitals or repeated punctuation."),
  note("Every message sent, every draft refused and the reason, and every model call are stored permanently and cannot be edited or deleted — the database refuses the change, not the application. A complete transcript can be produced for any seller."),

  h1("5.  Changes already made on compliance grounds"),
  p("Recorded so counsel can see what has been considered and rejected.", { run: { size: 21 } }),

  h2("5.1  Multiple accounts creating the appearance of competition"),
  p("The original brief proposed approaching a seller from six separate accounts to create the impression of competing interest. That was dropped before any code was written. It is misleading conduct under s18 of the ACL and puts the dealer licence at risk. What replaced it is a single identified dealership making one offer with one authorised negotiating range.", { run: { size: 21 } }),
  note("Several named buyers under one identified dealership remain in use — the identity is real, the dealership is named, and no impression of competition is created. §6.3 raises what that requires operationally."),

  h2("5.2  \"That's the most I've got room for\""),
  p("An earlier version said this on the first concession, when a second concession and a higher ceiling both existed above it. A seller told that and then offered more has been misled. The wording at §3.5 now states only what is true at the moment it is said.", { run: { size: 21 } }),

  h2("5.3  Sender identification on the first text message"),
  p("The disclosure at §3.1 covers the first Messenger message. Until recently, a conversation that moved to SMS produced a text from an unrecognised number that identified nobody and offered no way to stop it — a commercial electronic message without sender identification or a functional unsubscribe. §3.6 was added to close that.", { run: { size: 21 } }),

  h1("6.  Open points we want counsel's view on"),

  h2("6.1  No privacy collection notice"),
  p("The system asks for registration or VIN, runs a PPSR check, and stores the whole conversation permanently. No notice of collection is given to the seller at any point, and there is no link to a privacy policy in any message. We believe APP 5 requires one at or before the time of collection, and we have not drafted it because the wording should be counsel's. It would sit in the opening message at §3.1.", { run: { size: 21 } }),

  h2("6.2  Basis for sending the first SMS"),
  p("The seller provides a mobile number when asked for one in the Messenger conversation. Whether that establishes consent for a commercial text — inferred or express — is question 4. If express consent is required, the assistant must ask for it in terms, and we would add the wording to the discovery sequence.", { run: { size: 21 } }),

  h2("6.3  How many conversations one named buyer can hold"),
  p("Several named buyers operate under the one dealership. A name holding an implausible number of simultaneous conversations invites the inference that it is not a person, which is the impression §5.1 exists to avoid. The system now spreads leads across the configured names and limits how many live conversations each holds. We would like counsel's view on whether the names must correspond to actual employees.", { run: { size: 21 } }),

  h2("6.4  Retention"),
  p("Conversations, offers, valuations and model calls are stored permanently and cannot be altered or deleted — deliberately, because the record is what demonstrates what was said. We have not set a retention period and would like advice on whether one is required and what it should be.", { run: { size: 21 } }),

  h1("7.  Inventory"),
  p("Every message class, when it fires, and the provision it answers to.", { run: { size: 21 } }),
  table(
    ["Message", "When", "Provision"],
    [
      ["Opening with disclosure", "First contact, always", "MCTA 1986 (Vic); ACL s18"],
      ["Identifying as automated", "Whenever the seller asks", "ACL s18"],
      ["Discovery questions", "Until ten facts are recorded", "—"],
      ["Contradiction check", "Listing disagrees with a check", "ACL s18"],
      ["Offer", "Once a valuation exists", "ACL s18, s29(1)(i)"],
      ["Concession (×2)", "Seller counters", "ACL s18"],
      ["End of authority", "Concessions exhausted", "ACL s18"],
      ["Offer lapsed", "48 hours pass", "ACL s29(1)(i)"],
      ["First SMS prefix", "Channel moves to text", "Spam Act 2003 (Cth)"],
      ["STOP acknowledgement", "Seller opts out", "Spam Act 2003 (Cth)"],
      ["Follow-up (max 3)", "Seller goes quiet", "Spam Act 2003 (Cth)"],
      ["Closing", "Accepted, declined or stalled", "—"],
    ],
    [3400, 3400, 2560]
  ),
  note("Prepared by Live Luxe for the dealership's legal adviser. We are not lawyers and nothing here is legal advice; it is a complete statement of what the system says, for someone qualified to assess."),
];

const doc = new Document({
  creator: "Live Luxe",
  title: "Automated Seller Engagement — Messages for Legal Review",
  numbering: {
    config: [
      { reference: "dots", levels: [{ level: 0, format: LevelFormat.BULLET, text: "•", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 220 } } } }] },
      { reference: "qs", levels: [{ level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT, style: { paragraph: { indent: { left: 420, hanging: 300 } } } }] },
    ],
  },
  styles: { default: { document: { run: { font: "Calibri", size: 21 } } } },
  sections: [
    {
      properties: { page: { margin: { top: 1080, bottom: 1080, left: 1080, right: 1080 } } },
      footers: {
        default: new Footer({
          children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [new TextRun({ text: "Confidential — for legal review", size: 17, color: GREY })] })],
        }),
      },
      children: body,
    },
  ],
});

Packer.toBuffer(doc).then((b) => {
  fs.writeFileSync(process.argv[2] || "legal-review-pack.docx", b);
  console.log("written");
});
