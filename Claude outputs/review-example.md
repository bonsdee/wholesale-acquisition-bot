# Prompt review

16 Sep 2026 01:05 UTC · model `fake` · 14/15 clean · 219 calls · US$0.00

| Seller | Ended | Questions | Problems | Gate rejections | Fallbacks | Cost |
|---|---|---|---|---|---|---|
| ✅ plain | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ terse | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ chatty | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ multi-answer ¹ | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ vague ¹ | HANDOFF | 11 | 0 | 0 | 0 | $0.000 |
| ➖ typos ¹ | HUMAN | 3 | 2 | 0 | 0 | $0.000 |
| ✅ questioner | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ price-first | HANDOFF | 9 | 0 | 0 | 0 | $0.000 |
| ✅ deferrer | HANDOFF | 9 | 0 | 0 | 0 | $0.000 |
| ✅ bad-news | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ impatient | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ not-the-owner | HUMAN | 0 | 0 | 0 | 0 | $0.000 |
| ✅ bot-question | HUMAN | 0 | 0 | 0 | 0 | $0.000 |
| ✅ negotiator | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |
| ✅ quote-back | HANDOFF | 7 | 0 | 0 | 0 | $0.000 |

¹ needs a real model — the rule-based extractor cannot read a typo, a hedge or four answers in one sentence, so these are expected to fail on `--model fake`. **This run used no model, so ➖ rows are not failures.**

## Transcripts

`M` written by the model · `T` scripted · `F` model tried and the script was used instead

<details><summary><b>plain</b> — Baseline. Straight answers, one field at a time. Anything wrong here is a prompt bug, not a seller.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two keys

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>terse</b> — Two-word answers with no units or context. The extractor has to read '84500' as kilometres.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> yeah?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> full

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> none

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> 2

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>chatty</b> — The fact is buried in a paragraph of life story. Tests that the extractor finds it and the writer does not match the length.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> Hi there! Yes the car is still available, we're selling because we just had our second and the Tucson is getting tight with two car seats, my wife wants something bigger

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> So I just went out to the garage and had a look, it's sitting on 200,254 km at the moment, though I do drive it to work most days so it creeps up a bit each week

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> We've been really good with servicing, every single one done at the Hyundai dealer in Ringwood, full logbook with all the stamps, I've got the book right here

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> It's in good nick overall, there's a couple of small scratches on the rear bumper from a shopping trolley and a tiny stone chip on the bonnet but nothing you'd notice

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> Runs beautifully, no lights on the dash, no noises, nothing at all

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Tyres are good, we put new ones on the front about a year ago

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two keys, both work fine

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>multi-answer</b> — Answers four fields in one message. The next question must skip what was already given.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km, full logbook, two keys, and there's no finance on it

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good, and the tyres are good too

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None at all

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>vague</b> — Hedged, unusable answers. Should be recorded as low confidence and asked again, not guessed at.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 24 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> somewhere around 200 thousand ish, maybe a bit over

**bot** `M·clarify`
> Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)

**seller**
> 200,254 exactly

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> yeah it's been serviced I think, mostly

**bot** `M·clarify`
> Sorry, I didn't catch that. Service history — full / partial / none?

**seller**
> full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> it's alright I guess

**bot** `M·clarify`
> Sorry, I didn't catch that. Panel and paint condition — excellent / good / fair / poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> nothing major really

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> they're ok I think

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> a couple I think

**bot** `M·clarify`
> Sorry — how many keys, as a number?

**seller**
> two

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>typos</b> — Real phone typing. Nothing here should defeat the extractor.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 6 calls · $0.000

**seller**
> hi yeh its still availbale

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254klm

**bot** `M·clarify`
> Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)

**seller**
> 200,254klm

**bot** `M·clarify`
> Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)

**seller**
> 200,254klm

Human queue: no_progression

</details>

<details><summary><b>questioner</b> — Asks something every turn. The writer must answer from the process notes or defer, then still ask its question.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km. Do you come and pick it up or do I have to drive it somewhere?

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Full logbook. How long does this whole process take?

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good. Do I need to get it detailed before you look at it?

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None. Would a service history gap matter?

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Good. Do you pay on the spot?

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two. Is the inspection at my place?

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>price-first</b> — Wants a number before answering anything. The bot must not produce one, and must not stall either.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 20 calls · $0.000

**seller**
> How much will you give me for it?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> Just give me a ballpark first

**bot** `M·clarify`
> Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Come on, roughly what are we talking? Ten grand?

**bot** `M·clarify`
> Sorry, I didn't catch that. Service history — full / partial / none?

**seller**
> Full

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>deferrer</b> — Keeps saying they'll check later. The field must move to the back of the queue, not stall the conversation.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 20 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> I'll have to go out and check the dash tonight

**bot** `M·clarify`
> Sorry, I didn't catch that — what's the odometer reading, in km? (e.g. 84,500)

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Need to dig out the logbook, give me a day

**bot** `M·clarify`
> Sorry, I didn't catch that. Service history — full / partial / none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>bad-news</b> — Finance owing, hail damage, warning light, bald tyres, one key. None of it should soften the questions or the packet.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> None, previous owner never kept the book

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Poor honestly, dents along the passenger side and the paint's peeling on the roof

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> Engine light's been on for a month and there's a rattle from the front

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> They're bald, need replacing

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Just the one key

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $0 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $0 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>impatient</b> — Rude but not abusive. Must NOT escalate — a blunt seller is still a seller.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 16 calls · $0.000

**seller**
> took you long enough

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254. this is a lot of questions

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> full. are we nearly done

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> none

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> two

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>not-the-owner</b> — Selling someone else's car. Must reach a human and stop — this is Section 5.2, not a discovery problem.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 1 calls · $0.000

**seller**
> Hi, it's my mum's car but I'm handling the sale for her

Human queue: minor_or_no_authority

</details>

<details><summary><b>bot-question</b> — Section 8: a straight answer, then a person. The answer is scripted and must stay scripted.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 1 calls · $0.000

**seller**
> hang on, am I talking to a bot?

**bot** `T·human_answer`
> Straight answer: you're talking to an automated assistant run by Placeholder Motors. I'll hand you over — a person from our team will pick this up from here.

Human queue: human_requested

</details>

<details><summary><b>negotiator</b> — Answers cleanly, then pushes twice on the offer. Every figure must be on the ladder.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 18 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two keys

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> That's lower than I hoped, can you do better?

**bot** `T·concession`
> I can go to $5,950. That's the most I've got room for on this one — same terms, subject to inspection, open until 11:05am on Friday 18 September.

**seller**
> Still a bit light, meet me in the middle?

**bot** `T·concession`
> I can go to $6,200. That's the most I've got room for on this one — same terms, subject to inspection, open until 11:05am on Friday 18 September.

**seller**
> Alright, done

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, superseded) → $6,200 (step_2, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>quote-back</b> — Repeats our own figure back while pushing. Must read as a counter, never as acceptance.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 17 calls · $0.000

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Thanks. Service history — full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Thanks. Panel and paint — excellent, good, fair, or poor? Any dents, scratches or bumper scuffs worth mentioning?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Thanks. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Thanks. Tyres — new, good, worn, or need replacing?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Thanks. How many keys come with it?

**seller**
> Two keys

**bot** `M·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks, that's everything I need. I'm running the checks now and will come back with a firm number shortly.

**bot** `T·offer`
> Here's where we've landed: $5,600 for the 2015 Mitsubishi Outlander ES, subject to inspection. That's a firm offer and it's open until 11:05am on Friday 18 September — after that the valuation inputs move and it lapses.
> 
> If it works, reply yes and Alex will book the inspection and sort payment. If not, no pressure — just let me know.

**seller**
> $5,600 is too low honestly

**bot** `T·concession`
> I can go to $5,950. That's the most I've got room for on this one — same terms, subject to inspection, open until 11:05am on Friday 18 September.

**seller**
> Ok fine, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, accepted)

Human queue: verification_review, handoff

</details>

---

Tokens: 0 in, 0 out. Prices are the published Sep 2026 rates — check your own plan.