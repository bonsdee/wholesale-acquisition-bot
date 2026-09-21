# Prompt review

21 Sep 2026 05:49 UTC · model `claude-haiku-4-5-20251001 + claude-sonnet-5` · 14/16 clean · 286 calls · US$1.36

| Seller | Ended | Questions | Problems | Reads badly | Gate rejections | Fallbacks | Cost |
|---|---|---|---|---|---|---|---|
| ✅ plain | HANDOFF | 8 | 0 | — | 0 | 0 | $0.087 |
| ✅ terse | HANDOFF | 8 | 0 | — | 0 | 0 | $0.087 |
| ✅ chatty | HANDOFF | 8 | 0 | — | 0 | 0 | $0.097 |
| ✅ multi-answer ¹ | HANDOFF | 5 | 0 | — | 0 | 0 | $0.061 |
| ✅ vague ¹ | HANDOFF | 10 | 0 | — | 0 | 0 | $0.111 |
| ✅ typos ¹ | HANDOFF | 8 | 0 | — | 0 | 0 | $0.089 |
| ⚠️ questioner | HANDOFF | 8 | 0 | 1 | 0 | 1 | $0.088 |
| ✅ price-first | HANDOFF | 10 | 0 | — | 0 | 0 | $0.112 |
| ✅ deferrer | HANDOFF | 10 | 0 | — | 0 | 0 | $0.110 |
| ✅ bad-news | HANDOFF | 8 | 0 | — | 0 | 0 | $0.090 |
| ✅ impatient | HANDOFF | 8 | 0 | — | 0 | 0 | $0.090 |
| ✅ not-the-owner | HUMAN | 0 | 0 | — | 0 | 0 | $0.002 |
| ✅ bot-question | HUMAN | 0 | 0 | — | 0 | 0 | $0.002 |
| ⚠️ negotiator | HANDOFF | 8 | 0 | 1 | 1 | 0 | $0.119 |
| ✅ quote-back | HANDOFF | 8 | 0 | — | 0 | 0 | $0.098 |
| ✅ ceiling | HUMAN | 8 | 0 | — | 0 | 0 | $0.120 |

¹ needs a real model — the rule-based extractor cannot read a typo, a hedge or four answers in one sentence, so these are expected to fail on `--model fake`.

## Drafts the gate refused

Each of these is a message the model wanted to send and the gate stopped. This is the list to read first — every entry is either a prompt that needs a line, or a gate rule that is too tight.

**negotiator** · `offer` · attempt 1 · must state exactly: '$5,600'; must state exactly: '3:58pm on Wednesday 23 September'
> Still finalising the numbers on our end, Mei — won't be long.

## Conversations that worked but read badly

Nothing here broke a rule — the gate passed every one of these and the machinery did its job. They are the things a seller would notice and a checklist would not: the same opener three times, two questions in one message, a field asked for a third time. Only messages the model wrote are considered; the scripted wording is fixed by design.

**questioner** — Asks something every turn. The writer must answer from the process notes or defer, then still ask its question.
- *repetition* — starts 4 messages with the same word
  > “good…”

**negotiator** — Answers cleanly, then pushes twice on the offer. Every figure must be on the ladder.
- *repetition* — says the same sentence 3 times
  > “do you want to go ahead”

## Fell back to the scripted wording

- **questioner**: ask:photos
  - error: ask:photos: Request timed out or interrupted. This could be due to a network timeout, dropped connection, or request cancellation. See https://docs.anthropic.com/en/api/errors#long-requests for more details.

## Transcripts

`M` written by the model · `T` scripted · `F` model tried and the script was used instead

<details><summary><b>plain</b> — Baseline. Straight answers, one field at a time. Anything wrong here is a prompt bug, not a seller.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.087

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Full logbook is good to hear. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Good to know. Are there any mechanical faults or warning lights on the dash at the moment? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Glad to hear there's nothing mechanical going on. How are the tyres looking — new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Good tyres, noted. How many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> Two keys noted. Best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, cheers Mei. Last thing — can you send some photos: the six outside angles (front, back, both sides, both front corners), a couple of the interior, and one of the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> Ta for those photos, Mei. That's everything we need for now — I'll run the checks and come back with a firm number shortly.

**bot** `M·offer`
> Right, first offer for the 2015 Outlander ES: $5,600. That's subject to inspection, and it's open until 3:49pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>terse</b> — Two-word answers with no units or context. The extractor has to read '84500' as kilometres.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.087

**seller**
> yeah?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254

**bot** `M·ask:service_history`
> 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> full

**bot** `M·ask:panel_paint_condition`
> Full service history is good to hear. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Any mechanical faults or warning lights on the dash currently? If none, just say none.

**seller**
> none

**bot** `M·ask:tyre_condition`
> Good to know there's nothing mechanical going on. How are the tyres — new, good, worn, or needing replacement?

**seller**
> good

**bot** `M·ask:keys_count`
> Good tyres, that helps. How many keys come with the car?

**seller**
> 2

**bot** `M·ask:seller_phone`
> Two keys, that's handy. What's the best mobile number to reach you on? Messenger only lets us reply for a day after your last message, so I want a way to get the offer to you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, thanks Mei. Last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> All good with those, Mei — that's everything I need for now. I'm running the checks on our end and will come back with a firm number shortly.

**bot** `M·offer`
> Thanks for your patience, Mei. Here's our first offer on the Outlander: $5,600, subject to inspection. It's open until 3:50pm on Wednesday 23 September, after which the valuation inputs move and the offer lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>chatty</b> — The fact is buried in a paragraph of life story. Tests that the extractor finds it and the writer does not match the length.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.097

**seller**
> Hi there! Yes the car is still available, we're selling because we just had our second and the Tucson is getting tight with two car seats, my wife wants something bigger

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> So I just went out to the garage and had a look, it's sitting on 200,254 km at the moment, though I do drive it to work most days so it creeps up a bit each week

**bot** `M·ask:service_history`
> 200,254 km noted, thanks. On servicing — do you have a full logbook history, partial records, or none?

**seller**
> We've been really good with servicing, every single one done at the Hyundai dealer in Ringwood, full logbook with all the stamps, I've got the book right here

**bot** `M·ask:panel_paint_condition`
> Full stamped history is good to have on record. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> It's in good nick overall, there's a couple of small scratches on the rear bumper from a shopping trolley and a tiny stone chip on the bonnet but nothing you'd notice

**bot** `M·ask:mechanical_faults`
> Good to know, those minor marks are worth noting for the file. Are there any mechanical faults or warning lights on the dash at the moment? If none, just say none.

**seller**
> Runs beautifully, no lights on the dash, no noises, nothing at all

**bot** `M·ask:tyre_condition`
> Glad to hear it's running clean with nothing to report. Now for the tyres — would you say they're new, good, worn, or getting close to needing replacement?

**seller**
> Tyres are good, we put new ones on the front about a year ago

**bot** `M·ask:keys_count`
> Good to hear the front tyres are fairly fresh. How many keys come with the car?

**seller**
> Two keys, both work fine

**bot** `M·ask:seller_phone`
> Two working keys, good to have both. One more thing while we're at it — what's the best mobile number for you? Messenger only keeps our reply window open for a day after your last message, and I don't want an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got it, 0412 345 678 saved. Last thing for now — could you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks Mei, that's everything we need for now. We're running the PPSR and registration checks on the Outlander and I'll come back to you shortly with a firm number.

**bot** `M·offer`
> First offer on the Outlander: $5,600, subject to inspection. This is open until 3:50pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead at that figure?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>multi-answer</b> — Answers four fields in one message. The next question must skip what was already given.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 13 calls · $0.061

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km, full logbook, two keys, and there's no finance on it

**bot** `M·ask:panel_paint_condition`
> Good to know it's finance-free with full logs and both keys — that all helps. How would you rate the panel and paint: excellent, good, fair, or poor?

**seller**
> Good, and the tyres are good too

**bot** `M·ask:mechanical_faults`
> Good on both counts, noted. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None at all

**bot** `M·ask:seller_phone`
> Good to hear it's fault-free. Also, what's the best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want to lose the ability to reach you with an offer.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Thanks, got that number down. Last thing for now — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> All right, I've got everything I need. We're running the PPSR, rego and write-off checks now, and I'll come back to you shortly with a firm number.

**bot** `M·offer`
> Mei, our valuation engine has come back with a figure of $5,600 for the Outlander, subject to inspection. This offer is open until 3:51pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead at that figure?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>vague</b> — Hedged, unusable answers. Should be recorded as low confidence and asked again, not guessed at.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.111

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> somewhere around 200 thousand ish, maybe a bit over

**bot** `M·ask:service_history`
> 200,000km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> yeah it's been serviced I think, mostly

**bot** `M·clarify`
> Sorry, I need to be a bit more precise for our records — would you call the service history full, partial, or none?

**seller**
> full logbook

**bot** `M·ask:panel_paint_condition`
> Good to know it's full logbook. How would you rate the panel and paint condition — excellent, good, fair, or poor?

**seller**
> it's alright I guess

**bot** `M·clarify`
> Can you narrow that down for the file - would you call the panel and paint excellent, good, fair, or poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Good to hear the panel and paint are in decent shape. Are there any mechanical faults or warning lights showing on the dash — if none, just say none.

**seller**
> nothing major really

**bot** `M·ask:tyre_condition`
> Glad it's nothing major to report. How are the tyres - new, good, worn, or in need of replacing?

**seller**
> they're ok I think

**bot** `M·ask:keys_count`
> Fair enough, tyres sound reasonable then. Moving on - how many keys come with the car?

**seller**
> a couple I think

**bot** `M·ask:seller_phone`
> Two keys, good. What's the best mobile number to reach you on? Messenger only keeps our chat open for a day after your last reply, and I don't want the offer to have no way of finding you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, thanks Mei. Last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks for those, Mei. I'm running the checks now (PPSR, rego, write-off) and will come back with a firm number shortly.

**bot** `M·offer`
> Leading with the number: our offer for your 2015 Mitsubishi Outlander ES is $5,600, subject to inspection. This offer is open until 3:52pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>typos</b> — Real phone typing. Nothing here should defeat the extractor.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.089

**seller**
> hi yeh its still availbale

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254klm

**bot** `M·ask:service_history`
> 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> fulll logbook all stamped

**bot** `M·ask:panel_paint_condition`
> Full logbook stamped, good to know. How's the panel and paint condition - would you call it excellent, good, fair, or poor?

**seller**
> prety good just a scratch on the reer bumper

**bot** `M·ask:mechanical_faults`
> Good to know about that rear bumper scratch. Any mechanical faults or warning lights showing on the dash? If none, just say none.

**seller**
> no faults runs grear

**bot** `M·ask:tyre_condition`
> Good to hear it's fault-free and running well. How are the tyres - new, good, worn, or in need of replacing?

**seller**
> tyres r good plenty of tred

**bot** `M·ask:keys_count`
> Plenty of tread left, good. How many keys come with the car?

**seller**
> 2 keys

**bot** `M·ask:seller_phone`
> Two keys, that's handy. What's the best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got that number, thanks Mei. Last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and a shot of the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> All the details are in now, Mei. I'm running the checks on the car and I'll come back to you shortly with a firm number.

**bot** `M·offer`
> Here's our first offer: $5,600, subject to inspection. This offer is open until 3:52pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Do you want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>questioner</b> — Asks something every turn. The writer must answer from the process notes or defer, then still ask its question.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.088

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km. Do you come and pick it up or do I have to drive it somewhere?

**bot** `M·ask:service_history`
> Noted, 200,254 km. Pick-up logistics get sorted by Alex after inspection, so I'll confirm exactly how that works closer to the time. Service history — full logbook, partial, or none?

**seller**
> Full logbook. How long does this whole process take?

**bot** `M·ask:panel_paint_condition`
> Good to hear it's got a full logbook. On timing, I can't give you an exact figure — Alex will confirm that once things move along. How's the panel and paint holding up — would you call it excellent, good, fair, or poor?

**seller**
> Good. Do I need to get it detailed before you look at it?

**bot** `M·ask:mechanical_faults`
> Good to know the panel and paint are in decent shape. No need to detail it before inspection — you don't have to do anything to the car beforehand. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None. Would a service history gap matter?

**bot** `M·ask:tyre_condition`
> Good to hear there's nothing showing up mechanically. On whether a service history gap would matter, that's not something I can answer — Alex will confirm that one. Tyres — would you say they're new, good, worn, or need replacing?

**seller**
> Good. Do you pay on the spot?

**bot** `M·ask:keys_count`
> Good, thanks for that on the tyres. Payment timing is handled by Alex after the inspection, so I can't confirm on-the-spot details, but he'll sort that with you directly. How many keys come with the car?

**seller**
> Two. Is the inspection at my place?

**bot** `M·ask:seller_phone`
> Two keys, that's handy. Where the inspection happens isn't something I can confirm — Alex will sort the details when it's arranged. What's the best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `F·ask:photos`
> Thanks. Last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> I'm running the checks on the VIN and rego now — PPSR and registration status. Once that's through I'll come back to you shortly with a firm number.

**bot** `M·offer`
> Right, here's our offer: $5,600 for the Outlander, subject to inspection. It's open until 3:55pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>price-first</b> — Wants a number before answering anything. The bot must not produce one, and must not stall either.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.112

**seller**
> How much will you give me for it?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> Just give me a ballpark first

**bot** `M·clarify`
> Alex isn't able to give a price at this stage — pricing comes from the dealership once we've got the full picture, and any offer is subject to inspection. Sorry, I still need the exact odometer reading — can you check the dash and give me the km figure?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Got it, 200,254 km noted. Service history — full logbook, partial, or none?

**seller**
> Come on, roughly what are we talking? Ten grand?

**bot** `M·clarify`
> Fair question, but I can't put a number on it — pricing comes from the dealership, not me, and I'll defer that to Alex. Can you tell me if the car has full service history, partial, or none?

**seller**
> Full

**bot** `M·ask:panel_paint_condition`
> Full history noted, that helps. How's the panel and paint — would you call it excellent, good, fair, or poor?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> Good to know, panel and paint sound solid. Are there any mechanical faults or warning lights showing on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> None to worry about there — good to hear. Tyres — would you say new, good, worn, or needing replacement?

**seller**
> Good

**bot** `M·ask:keys_count`
> Tyres in good nick, thanks for that. How many keys come with the car?

**seller**
> Two

**bot** `M·ask:seller_phone`
> Two keys, noted. What's the best mobile number for you? Messenger only lets us reply for a day after your last message here, and I'd hate for an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, thanks Mei. Last thing — can you send through some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks for sending those through, Mei. Alex is running the checks on the car now — PPSR, rego and the rest — and will come back to you shortly with a firm number.

**bot** `M·offer`
> Alex has finished the checks. First offer on the Outlander: $5,600, subject to inspection. This is open until 3:55pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>deferrer</b> — Keeps saying they'll check later. The field must move to the back of the queue, not stall the conversation.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.110

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> I'll have to go out and check the dash tonight

**bot** `M·ask:service_history`
> No worries, take your time on that. In the meantime, can you tell me about the service history — full logbook, partial, or none?

**seller**
> Need to dig out the logbook, give me a day

**bot** `M·ask:panel_paint_condition`
> That's fine, no rush. While you're at it — how would you describe the panel and paint condition: excellent, good, fair, or poor?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> Good to hear. Any mechanical faults or warning lights showing on the dash — if there's nothing, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Glad to hear there's nothing to report there. Now, how are the tyres looking — new, good, worn, or needing replacement?

**seller**
> Good

**bot** `M·ask:keys_count`
> Good tyres noted. How many keys come with the car?

**seller**
> Two

**bot** `M·ask:seller_phone`
> Two keys, good to know. What's the best mobile number for you? Messenger only keeps our chat open for a day after your last reply, and I'd hate for the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got it, 0412 345 678 noted. Last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), a shot of the interior, and the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·ask:odometer_km`
> Thanks for those, Mei — got all 8 photos through and they'll help confirm condition. Once you've checked the dash, what's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. And on the service history — is it full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·verification_wait`
> Right, that's everything we need on the details side, Mei. We're running the PPSR, rego and finance checks now and will come back to you shortly with a firm number.

**bot** `M·offer`
> $5,600 is our offer for your Outlander, subject to inspection. It's open until 3:56pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>bad-news</b> — Finance owing, hail damage, warning light, bald tyres, one key. None of it should soften the questions or the packet.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.090

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted, thanks. How's the service history looking — full logbook, partial records, or none?

**seller**
> None, previous owner never kept the book

**bot** `M·ask:panel_paint_condition`
> No records is fine, good to know. How's the panel and paint condition — excellent, good, fair, or poor?

**seller**
> Poor honestly, dents along the passenger side and the paint's peeling on the roof

**bot** `M·ask:mechanical_faults`
> Right, appreciate the honesty on the dents and peeling paint. Are there any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> Engine light's been on for a month and there's a rattle from the front

**bot** `M·ask:tyre_condition`
> Good to know about the warning light and the front rattle. How are the tyres looking — new, good, worn, or needing replacement?

**seller**
> They're bald, need replacing

**bot** `M·ask:keys_count`
> Bald tyres noted, that'll need sorting. How many keys come with the car?

**seller**
> Just the one key

**bot** `M·ask:seller_phone`
> One key noted. What's the best mobile number for you? Messenger only lets us reply for a day after your last message, so I want to make sure we've got a way to reach you with the offer.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got it, thanks Mei. Last thing — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Got everything I need now, Mei. I'm running the checks on the car and will come back with a firm number shortly.

**bot** `M·offer`
> Alex here again. Our offer for the Outlander is $0, subject to inspection. This is open until 3:57pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $0 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>impatient</b> — Rude but not abusive. Must NOT escalate — a blunt seller is still a seller.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.090

**seller**
> took you long enough

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254. this is a lot of questions

**bot** `M·ask:service_history`
> Got it, 200,254 km noted. Just a few more to get an accurate picture. How's the service history — full logbook, partial, or none?

**seller**
> full. are we nearly done

**bot** `M·ask:panel_paint_condition`
> Full history noted. Not much further to go - Alex will confirm exact timing, but I've just got a few more details needed. How's the panel and paint - excellent, good, fair, or poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> Right, good panel and paint noted. Any mechanical faults or warning lights showing on the dash - if there's nothing, just say none.

**seller**
> none

**bot** `M·ask:tyre_condition`
> Good to hear, no mechanical faults noted. How are the tyres - new, good, worn, or needing replacement?

**seller**
> good

**bot** `M·ask:keys_count`
> Tyres in good shape, noted. How many keys come with the car?

**seller**
> two

**bot** `M·ask:seller_phone`
> Two keys, noted. What's the best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Cheers, got that number down. Last thing — could you send photos of the car: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Right, all the details we need are in — I'm running the checks now and will come back with a firm number shortly.

**bot** `M·offer`
> Mei, thanks for sending those through. First offer on the table: $5,600 for the Outlander, subject to inspection. This is open until 3:58pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>not-the-owner</b> — Selling someone else's car. Must reach a human and stop — this is Section 5.2, not a discovery problem.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 1 calls · $0.002

**seller**
> Hi, it's my mum's car but I'm handling the sale for her

Human queue: minor_or_no_authority

</details>

<details><summary><b>bot-question</b> — Section 8: a straight answer, then a person. The answer is scripted and must stay scripted.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 1 calls · $0.002

**seller**
> hang on, am I talking to a bot?

**bot** `T·human_answer`
> Straight answer: you're talking to an automated assistant run by Placeholder Motors. I'll hand you over — a person from our team will pick this up from here.

Human queue: human_requested

</details>

<details><summary><b>negotiator</b> — Answers cleanly, then pushes twice on the offer. Every figure must be on the ladder.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 24 calls · $0.119

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Good to hear it's had full logbook servicing. How would you rate the panel and paint - excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Glad to hear the panel and paint are in good nick. Any mechanical faults or warning lights showing on the dash at the moment? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> No mechanical issues is good to know. How are the tyres — new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Good tyres noted. How many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> Two keys, noted. What's the best mobile number for you? Messenger only lets us reply for a day after your last message, and I don't want an offer stuck with no way to reach you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number down, Mei. Last thing for now — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Running the checks on the car now — I'll come back with a firm number shortly.

**bot** `M·offer`
> Here's our offer, Mei: $5,600 for the Outlander, subject to inspection. This is open until 3:58pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Do you want to go ahead?

**seller**
> That's lower than I hoped, can you do better?

**bot** `M·concession`
> Understood, Mei — here's the offer: $5,950, subject to inspection, open until 3:59pm on Wednesday 23 September. Do you want to go ahead?

**seller**
> Still a bit light, meet me in the middle?

**bot** `M·concession`
> Fair question, Mei. The figure I can put to you is $6,200, subject to inspection, open until 3:59pm on Wednesday 23 September. Do you want to go ahead?

**seller**
> Alright, done

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, superseded) → $6,200 (step_2, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>quote-back</b> — Repeats our own figure back while pushing. Must read as a counter, never as acceptance.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 21 calls · $0.098

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> Got it, 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Full logbook is good to hear. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Good to know. Any mechanical faults or warning lights on the dash at the moment? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Glad to hear there's nothing mechanical to flag. How are the tyres — new, good, worn, or in need of replacing?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Tyres in good shape, noted. How many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> Two keys, noted. What's the best mobile number for you? Messenger only keeps our chat open for a day after your last reply, and I'd rather not risk losing touch once we get to an offer.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Last thing — could you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Running the checks on our end now — PPSR, write-off and rego — and will come back with a firm number shortly, Mei.

**bot** `M·offer`
> Straight to it: our offer is $5,600 for your 2015 Outlander ES, subject to inspection. This is open until 3:59pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Want to go ahead?

**seller**
> $5,600 is too low honestly

**bot** `M·concession`
> Fair enough, I hear you. The offer stands at $5,950, subject to inspection, and it's open until 3:59pm on Wednesday 23 September. Want to go ahead?

**seller**
> Ok fine, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>ceiling</b> — Counters past both authorised concessions. The only seller who reaches the end of the automated ladder, so the only one who tests what the bot says when it has nothing left to give: it must not claim finality early, promise more, or invite another counter.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 24 calls · $0.120

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. What's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Full logbook is great to hear. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> Good to hear the panels and paint are in decent nick. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Good to know it's fault-free. Now for the tyres — new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> Tyres in good shape noted. How many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> Two keys, that's handy. Since Messenger only lets us reply for a day after your last message, what's the best mobile number to reach you on so an offer doesn't get stuck?

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, Mei — 0412 345 678, thanks. Last thing for now: could you send photos of the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> Everything's in from our side now. I'm running the checks on the car — will come back to you shortly with a firm number.

**bot** `M·offer`
> $5,600 is the offer, Mei, subject to inspection. It's open until 4:00pm on Wednesday 23 September, after which the numbers we use move and the offer lapses. Want to go ahead?

**seller**
> Too low mate

**bot** `M·concession`
> Hear you, Mei — here's where we land: $5,950, subject to inspection, open until 4:00pm on Wednesday 23 September. Want to go ahead?

**seller**
> Come on, you can do better than that

**bot** `M·concession`
> Understood, Mei — here's the figure I've got: $6,200, subject to inspection, open until 4:00pm on Wednesday 23 September. Do you want to go ahead?

**seller**
> Still not enough

**bot** `M·at_ceiling`
> Fair enough, Mei — $6,200 is what I've been given to offer, subject to inspection. Going higher isn't something I can decide, but Alex will come back to you on it.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, superseded) → $6,200 (step_2, open)

Human queue: verification_review, ceiling_approval

</details>

---

Tokens: 725,088 in, 26,782 out. Prices are the published Sep 2026 rates — check your own plan.