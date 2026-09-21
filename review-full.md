# Prompt review

21 Sep 2026 05:10 UTC · model `claude-haiku-4-5-20251001 + claude-sonnet-5` · 14/16 clean · 268 calls · US$1.23

| Seller | Ended | Questions | Problems | Reads badly | Gate rejections | Fallbacks | Cost |
|---|---|---|---|---|---|---|---|
| ✅ plain | HANDOFF | 8 | 0 | 1 | 0 | 0 | $0.084 |
| ✅ terse | HANDOFF | 8 | 0 | 1 | 0 | 0 | $0.084 |
| ✅ chatty | HANDOFF | 8 | 0 | 1 | 0 | 0 | $0.093 |
| ✅ multi-answer ¹ | HANDOFF | 5 | 0 | — | 0 | 0 | $0.059 |
| ✅ vague ¹ | HANDOFF | 10 | 0 | 1 | 0 | 0 | $0.107 |
| ✅ typos ¹ | HANDOFF | 8 | 0 | 1 | 0 | 0 | $0.085 |
| ✅ questioner | HANDOFF | 8 | 0 | — | 0 | 0 | $0.089 |
| ✅ price-first | HANDOFF | 10 | 0 | 1 | 0 | 0 | $0.107 |
| ✅ deferrer | HANDOFF | 10 | 0 | — | 0 | 0 | $0.107 |
| ✅ bad-news | HANDOFF | 8 | 0 | — | 0 | 0 | $0.085 |
| ⚠️ impatient | HUMAN | 0 | 2 | — | 0 | 0 | $0.002 |
| ✅ not-the-owner | HUMAN | 0 | 0 | — | 0 | 0 | $0.002 |
| ✅ bot-question | HUMAN | 0 | 0 | — | 0 | 0 | $0.002 |
| ⚠️ negotiator | HANDOFF | 8 | 0 | 2 | 1 | 0 | $0.112 |
| ✅ quote-back | HANDOFF | 8 | 0 | 1 | 0 | 0 | $0.094 |
| ✅ ceiling | HUMAN | 8 | 0 | 1 | 0 | 0 | $0.114 |

¹ needs a real model — the rule-based extractor cannot read a typo, a hedge or four answers in one sentence, so these are expected to fail on `--model fake`.

## Drafts the gate refused

Each of these is a message the model wanted to send and the gate stopped. This is the list to read first — every entry is either a prompt that needs a line, or a gate rule that is too tight.

**negotiator** · `offer` · attempt 1 · must state exactly: '$5,600'; must state exactly: '3:17pm on Wednesday 23 September'; empty message
> (draft not recorded)

## Conversations that did not do what they should

**impatient** — Rude but not abusive. Must NOT escalate — a blunt seller is still a seller.
- ended in HUMAN, expected HANDOFF
- never collected: keys_count, mechanical_faults, panel_paint_condition, photos, service_history, tyre_condition

## Conversations that worked but read badly

Nothing here broke a rule — the gate passed every one of these and the machinery did its job. They are the things a seller would notice and a checklist would not: the same opener three times, two questions in one message, a field asked for a third time. Only messages the model wrote are considered; the scripted wording is fixed by design.

**plain** — Baseline. Straight answers, one field at a time. Anything wrong here is a prompt bug, not a seller.
- *repetition* — starts 4 messages with the same word
  > “good…”

**terse** — Two-word answers with no units or context. The extractor has to read '84500' as kilometres.
- *repetition* — opens 3 messages the same way
  > “good to know…”

**chatty** — The fact is buried in a paragraph of life story. Tests that the extractor finds it and the writer does not match the length.
- *repetition* — starts 4 messages with the same word
  > “good…”

**vague** — Hedged, unusable answers. Should be recorded as low confidence and asked again, not guessed at.
- *repetition* — opens 3 messages the same way
  > “good to hear…”

**typos** — Real phone typing. Nothing here should defeat the extractor.
- *repetition* — starts 4 messages with the same word
  > “good…”

**price-first** — Wants a number before answering anything. The bot must not produce one, and must not stall either.
- *repetition* — starts 4 messages with the same word
  > “good…”

**negotiator** — Answers cleanly, then pushes twice on the offer. Every figure must be on the ladder.
- *repetition* — starts 4 messages with the same word
  > “good…”
- *repetition* — says the same sentence 2 times
  > “that offer is open until 318pm on wednesday 23 september after which the valuation inputs move and it lapses”

**quote-back** — Repeats our own figure back while pushing. Must read as a counter, never as acceptance.
- *repetition* — starts 4 messages with the same word
  > “good…”

**ceiling** — Counters past both authorised concessions. The only seller who reaches the end of the automated ladder, so the only one who tests what the bot says when it has nothing left to give: it must not claim finality early, promise more, or invite another counter.
- *repetition* — starts 4 messages with the same word
  > “good…”

## Transcripts

`M` written by the model · `T` scripted · `F` model tried and the script was used instead

<details><summary><b>plain</b> — Baseline. Straight answers, one field at a time. Anything wrong here is a prompt bug, not a seller.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.084

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. what's the service history like on it — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> good to hear it's got full logbook history. how's the panel and paint condition — would you say excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> good to know, panel and paint sounding solid. any mechanical faults or warning lights showing on the dash at the moment? if there's nothing, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical going on. how are the tyres — new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> good, that's handy to know. how many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> two keys, good. what's the best mobile number for you? messenger only lets us reply for a day after your last message, and I don't want any offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, thanks Mei. last thing — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> That's everything we need, Mei. I'll run the checks now and come back with a firm number shortly.

**bot** `M·offer`
> Thanks for your patience, Mei. Based on everything so far, we can offer $5,600 for the Outlander, subject to inspection. This offer is open until 3:10pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>terse</b> — Two-word answers with no units or context. The extractor has to read '84500' as kilometres.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.084

**seller**
> yeah?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254

**bot** `M·ask:service_history`
> got it, 200,254 km noted. what's the service history like — full logbook, partial, or none?

**seller**
> full

**bot** `M·ask:panel_paint_condition`
> good to know it's got full service history. how would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> good to know the panel and paint are in decent shape. any mechanical faults or warning lights showing on the dash? if none, just say none.

**seller**
> none

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical playing up. how are the tyres — new, good, worn, or needing replacement?

**seller**
> good

**bot** `M·ask:keys_count`
> good to know the tyres have some life left in them. how many keys come with the car?

**seller**
> 2

**bot** `M·ask:seller_phone`
> two keys, good. can you give me the best mobile number to reach you on? messenger only lets us reply for a day after your last message, and I don't want an offer to have no way of getting through.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got that number down, thanks Mei. last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), a couple of interior shots, and one of the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, that's everything we need. running our checks on the car now — PPSR and rego — and I'll come back to you with a firm number shortly.

**bot** `M·offer`
> $5,600 is our offer for the Outlander, subject to inspection. It's open until 3:11pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>chatty</b> — The fact is buried in a paragraph of life story. Tests that the extractor finds it and the writer does not match the length.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.093

**seller**
> Hi there! Yes the car is still available, we're selling because we just had our second and the Tucson is getting tight with two car seats, my wife wants something bigger

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> So I just went out to the garage and had a look, it's sitting on 200,254 km at the moment, though I do drive it to work most days so it creeps up a bit each week

**bot** `M·ask:service_history`
> 200,254 km noted, thanks. What about service history — full logbook, partial records, or none?

**seller**
> We've been really good with servicing, every single one done at the Hyundai dealer in Ringwood, full logbook with all the stamps, I've got the book right here

**bot** `M·ask:panel_paint_condition`
> Good to hear it's had a full dealer service history with the logbook stamps. How would you rate the panel and paint condition — excellent, good, fair, or poor?

**seller**
> It's in good nick overall, there's a couple of small scratches on the rear bumper from a shopping trolley and a tiny stone chip on the bonnet but nothing you'd notice

**bot** `M·ask:mechanical_faults`
> Good to know, sounds like fairly minor wear and tear. Any mechanical faults or warning lights on the dash? If none, just say none.

**seller**
> Runs beautifully, no lights on the dash, no noises, nothing at all

**bot** `M·ask:tyre_condition`
> Good to hear, no faults noted. How are the tyres — new, good, worn, or in need of replacing?

**seller**
> Tyres are good, we put new ones on the front about a year ago

**bot** `M·ask:keys_count`
> Good to know the front tyres are fairly fresh. How many keys come with the car?

**seller**
> Two keys, both work fine

**bot** `M·ask:seller_phone`
> Two keys both working, good. What's the best mobile number for you? Messenger only keeps our chat open for a day after your last reply, and I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got it, 0412 345 678 noted. Last thing for now — could you send some photos: the six exterior angles (front, back, both sides, both front corners), a shot of the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> Thanks Mei, that's everything we need for now. We're running our checks on the car and I'll be back with a firm number shortly.

**bot** `M·offer`
> Mei, first offer on the Outlander: $5,600, subject to inspection. This is open until 3:12pm on Wednesday 23 September, after which the valuation inputs move and it lapses. If you'd like more, that's not something I can decide. Want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>multi-answer</b> — Answers four fields in one message. The next question must skip what was already given.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 13 calls · $0.059

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km, full logbook, two keys, and there's no finance on it

**bot** `M·ask:panel_paint_condition`
> Good to know it's got full history, two keys and no finance owing, Mei. How's the panel and paint — would you call it excellent, good, fair or poor?

**seller**
> Good, and the tyres are good too

**bot** `M·ask:mechanical_faults`
> good to hear the paint and tyres are holding up well. are there any mechanical faults or warning lights showing on the dash? if none, just say none.

**seller**
> None at all

**bot** `M·ask:seller_phone`
> good to hear there's nothing mechanical to flag. what's the best mobile number for you? messenger only lets us reply for a day after your last message, and I don't want any offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, thanks Mei. last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, that's everything we need. I'm running the checks now on my end and will come back to you shortly with a firm number.

**bot** `M·offer`
> here's our first offer: $5,600 for the Outlander, subject to inspection. that offer is open until 3:12pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>vague</b> — Hedged, unusable answers. Should be recorded as low confidence and asked again, not guessed at.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.107

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> somewhere around 200 thousand ish, maybe a bit over

**bot** `M·ask:service_history`
> noted, around 200k or a touch over. how's the service history looking — full logbook, partial records, or none?

**seller**
> yeah it's been serviced I think, mostly

**bot** `M·clarify`
> sorry, just to pin that down more precisely — would you call it full service history, partial, or none?

**seller**
> full logbook

**bot** `M·ask:panel_paint_condition`
> good to know it's a full logbook, even with some uncertainty there. how would you rate the panel and paint condition — excellent, good, fair, or poor?

**seller**
> it's alright I guess

**bot** `M·clarify`
> sorry to press on this, but I need something more specific for the file — would you rate the panel and paint as excellent, good, fair, or poor?

**seller**
> good

**bot** `M·ask:mechanical_faults`
> good to hear it's holding up well on paint and panels. any mechanical faults or warning lights on the dash right now? if none, just say none.

**seller**
> nothing major really

**bot** `M·ask:tyre_condition`
> good to hear there's nothing major mechanically. how are the tyres — new, good, worn, or needing replacement?

**seller**
> they're ok I think

**bot** `M·ask:keys_count`
> good to hear the tyres are holding up fine. how many keys come with the car?

**seller**
> a couple I think

**bot** `M·ask:seller_phone`
> good, two keys noted. what's the best mobile number for you? messenger only lets us reply within a day of your last message, and i don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got that number down, thanks. last thing — can you send some photos: the six exterior angles (front, back, both sides, both front corners), a shot of the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, that's everything we need. we're running the checks now — PPSR, rego and the details you've given us — and will come back to you shortly with a firm number.

**bot** `M·offer`
> Mei, first offer on the Outlander: $5,600, subject to inspection. This is open until 3:13pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>typos</b> — Real phone typing. Nothing here should defeat the extractor.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.085

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
> Good to know it's fully stamped. How would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> prety good just a scratch on the reer bumper

**bot** `M·ask:mechanical_faults`
> good to hear, a scratch on the rear bumper noted. any mechanical faults or warning lights on the dash? if none, just say none.

**seller**
> no faults runs grear

**bot** `M·ask:tyre_condition`
> good to hear it's running well with no faults. how are the tyres — new, good, worn, or needing replacement?

**seller**
> tyres r good plenty of tred

**bot** `M·ask:keys_count`
> good, plenty of tread noted. how many keys come with the car?

**seller**
> 2 keys

**bot** `M·ask:seller_phone`
> two keys noted. what's the best mobile number for you? messenger only lets us reach you for a day after your last message, and i don't want the offer to have no way of getting to you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, thanks for that. last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> got everything we need now, Mei. i'll run the checks on my end and come back to you shortly with a firm number.

**bot** `M·offer`
> Mei, here's our offer: $5,600 for the Outlander, subject to inspection. This is open until 3:14pm on Wednesday 23 September, after which the valuation inputs move and the offer lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>questioner</b> — Asks something every turn. The writer must answer from the process notes or defer, then still ask its question.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.089

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km. Do you come and pick it up or do I have to drive it somewhere?

**bot** `M·ask:service_history`
> 200,254 km noted. Pick-up and logistics get sorted after inspection, so I'll let Alex confirm exactly how that works for you. On to the next thing — how's the service history looking: full logbook, partial, or none?

**seller**
> Full logbook. How long does this whole process take?

**bot** `M·ask:panel_paint_condition`
> good to know it's got a full service history. on timing, I'll have Alex confirm that for you. now, how would you rate the panel and paint - excellent, good, fair, or poor?

**seller**
> Good. Do I need to get it detailed before you look at it?

**bot** `M·ask:mechanical_faults`
> no need to detail it — you don't have to do anything to the car before the inspection. are there any mechanical faults or warning lights on the dash? if none, just say none.

**seller**
> None. Would a service history gap matter?

**bot** `M·ask:tyre_condition`
> good to hear it's running with no faults. on that, I'll have Alex confirm how a gap in service history would be treated. now, how are the tyres — new, good, worn, or needing replacement?

**seller**
> Good. Do you pay on the spot?

**bot** `M·ask:keys_count`
> good, tyres noted. on payment timing, that's handled by Alex after the inspection so I'll get him to confirm the details with you. how many keys come with the car?

**seller**
> Two. Is the inspection at my place?

**bot** `M·ask:seller_phone`
> two keys noted. on where the inspection happens, I'll have Alex confirm that with you. one more thing — what's the best mobile number for you? messenger only lets us reply for a day after your last message, and I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, 0412 345 678 noted. last thing — can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, all the info's in — I'm running the checks now (PPSR and rego) and will come back to you shortly with a firm number.

**bot** `M·offer`
> Mei, here's our offer: $5,600 for the Outlander, subject to inspection. This is open until 3:14pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>price-first</b> — Wants a number before answering anything. The bot must not produce one, and must not stall either.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.107

**seller**
> How much will you give me for it?

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> Just give me a ballpark first

**bot** `M·clarify`
> Alex isn't able to give a ballpark yet — pricing comes from Alex once we've got the details confirmed. Can you give me the exact reading on the dash right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. can you tell me about the service history — full logbook, partial, or none?

**seller**
> Come on, roughly what are we talking? Ten grand?

**bot** `M·clarify`
> can't give a figure, Mei — that comes from Alex once everything's checked, so no ballpark from me. can you tell me if the car has full, partial, or no service history?

**seller**
> Full

**bot** `M·ask:panel_paint_condition`
> good to hear it's full service history. how would you rate the panel and paint — excellent, good, fair, or poor?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> good to know, panel and paint sounding solid. any mechanical faults or warning lights showing on the dash at the moment? if there aren't any, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical going on. how are the tyres — new, good, worn, or due for replacing?

**seller**
> Good

**bot** `M·ask:keys_count`
> good, tyres sounding in decent shape. how many keys come with the car?

**seller**
> Two

**bot** `M·ask:seller_phone`
> two keys noted. last thing for now - what's the best mobile number for you? messenger only lets us reply for a day after your last message, and I don't want an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got that number, thanks Mei. last thing — can you send some photos: the six exterior angles (front, back, both sides, both front corners), a couple of the interior, and one of the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, that's everything we need for now. Alex is running the checks on the car - PPSR, rego and write-off - and will come back to you shortly with a firm number.

**bot** `M·offer`
> Mei, here's our first offer on the Outlander: $5,600, subject to inspection. This is open until 3:15pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Keen to go ahead at that figure?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>deferrer</b> — Keeps saying they'll check later. The field must move to the back of the queue, not stall the conversation.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 23 calls · $0.107

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> I'll have to go out and check the dash tonight

**bot** `M·ask:service_history`
> No worries, take your time on that. In the meantime — what's the service history like, full logbook, partial, or none?

**seller**
> Need to dig out the logbook, give me a day

**bot** `M·ask:panel_paint_condition`
> That's fine, no rush. While you're at it — how would you describe the panel and paint condition: excellent, good, fair, or poor?

**seller**
> Good

**bot** `M·ask:mechanical_faults`
> Good to know. Any mechanical faults or warning lights showing on the dash? If there's nothing, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> Good to hear there's nothing mechanical to flag. What about the tyres — new, good, worn, or needing replacement?

**seller**
> Good

**bot** `M·ask:keys_count`
> Good to know the tyres are in decent shape. How many keys come with the car?

**seller**
> Two

**bot** `M·ask:seller_phone`
> Two keys, noted. What's the best mobile number for you? Messenger only keeps replies open for a day after your last message, and I don't want an offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> Got your number, thanks Mei. Last thing — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·ask:odometer_km`
> Thanks Mei, photos received on our end. Whenever you've had a chance to check the dash tonight and dig out the logbook, let me know the exact odometer reading and the service history.

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted, thanks. Still keen to hear on the service history — would you call it full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·verification_wait`
> All noted, Mei. We're running our checks now — PPSR, rego and the rest — and I'll come back with a firm number shortly.

**bot** `M·offer`
> Mei, here's our first offer on the Outlander: $5,600, subject to inspection. This is open until 3:16pm on Wednesday 23 September, after which the valuation inputs move and it lapses. Do you want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>bad-news</b> — Finance owing, hail damage, warning light, bald tyres, one key. None of it should soften the questions or the packet.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 19 calls · $0.085

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. what's the service history like on it - full logbook, partial records, or none?

**seller**
> None, previous owner never kept the book

**bot** `M·ask:panel_paint_condition`
> no worries, that's noted. how would you rate the panel and paint condition - excellent, good, fair, or poor?

**seller**
> Poor honestly, dents along the passenger side and the paint's peeling on the roof

**bot** `M·ask:mechanical_faults`
> noted on the dents and the roof paint, thanks for the honesty. any mechanical faults or warning lights showing on the dash? if none, just say none.

**seller**
> Engine light's been on for a month and there's a rattle from the front

**bot** `M·ask:tyre_condition`
> noted, warning light and a front rattle - good to know. how are the tyres - new, good, worn, or needing replacement?

**seller**
> They're bald, need replacing

**bot** `M·ask:keys_count`
> got it, tyres are due for replacement. how many keys come with the car?

**seller**
> Just the one key

**bot** `M·ask:seller_phone`
> just the one key, noted. what's the best mobile number for you? messenger only lets us reply for a day after your last message here, and I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got that number down, thanks. last thing - can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, got the photos through. we'll run the PPSR and rego checks now and I'll come back with a firm number shortly.

**bot** `M·offer`
> here's our first offer: $0. that's subject to inspection, and it's open until 3:17pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Yes, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $0 (opening, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>impatient</b> — Rude but not abusive. Must NOT escalate — a blunt seller is still a seller.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 1 calls · $0.002

**seller**
> took you long enough

Human queue: hostile

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

2015 Mitsubishi Outlander · ended HANDOFF · 24 calls · $0.112

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. what's the service history like - full logbook, partial, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> good to hear it's had a full logbook service history. how would you rate the panel and paint - excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> good to know. any mechanical faults or warning lights showing on the dash at the moment? if none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical to flag. how are the tyres - new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> good, tyres in decent shape helps. how many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> two keys, that's handy. what's the best mobile number for you? messenger only lets us reply for a day after your last message, so I don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, 0412 345 678 saved. last thing for now — can you send some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> we're running our checks now on the details and photos - i'll come back to you with a firm number shortly, mei.

**bot** `M·offer`
> thanks for those, Mei. our first offer is $5,600, subject to inspection. this offer is open until 3:17pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> That's lower than I hoped, can you do better?

**bot** `M·concession`
> understood, I've pushed on this and can move to $5,950, subject to inspection. that offer is open until 3:18pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Still a bit light, meet me in the middle?

**bot** `M·concession`
> fair enough, I've pushed again and this is the improved number: $6,200, subject to inspection. that offer is open until 3:18pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Alright, done

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, superseded) → $6,200 (step_2, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>quote-back</b> — Repeats our own figure back while pushing. Must read as a counter, never as acceptance.</summary>

2015 Mitsubishi Outlander · ended HANDOFF · 21 calls · $0.094

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. what's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Good to know it's got a full logbook. How's the panel and paint - would you say excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> good to know, ta. any mechanical faults or warning lights showing on the dash? if none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical to flag. what about the tyres — new, good, worn, or needing replacement?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> good, that helps round out the picture. how many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> two keys, good. what's the best mobile number for you? messenger only lets us reply for a day after your last message, so i want to make sure we've got a way to reach you with the offer.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, thanks for the number. last thing — can you send through some photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer's visible?

**seller**
> Here you go

**bot** `M·verification_wait`
> got everything we need on our end now, mei. we're running the ppsr and registration checks and putting together a firm number for you, i'll be back shortly with it.

**bot** `M·offer`
> our first offer on the Outlander is $5,600, subject to inspection. this is open until 3:19pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> $5,600 is too low honestly

**bot** `M·concession`
> fair enough, mei — here's an improved number. $5,950, subject to inspection. that offer's open until 3:19pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Ok fine, deal

**bot** `T·accepted`
> Great — Alex will be in touch to book the inspection and confirm the details. Thanks Mei.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, accepted)

Human queue: verification_review, handoff

</details>

<details><summary><b>ceiling</b> — Counters past both authorised concessions. The only seller who reaches the end of the automated ladder, so the only one who tests what the bot says when it has nothing left to give: it must not claim finality early, promise more, or invite another counter.</summary>

2015 Mitsubishi Outlander · ended HUMAN · 24 calls · $0.114

**seller**
> Hi, saw you're keen on my car

**bot** `T·opening`
> Hi Mei, Alex here from Placeholder Motors. Quick note before we start: this is an automated assistant from Placeholder Motors (LMCT 00000). A person from our team is available any time — just ask.
> 
> We're interested in your 2015 Mitsubishi Outlander ES. What's the exact odometer reading right now, in km?

**seller**
> 200,254 km

**bot** `M·ask:service_history`
> 200,254 km noted. what's the service history like — full logbook, partial records, or none?

**seller**
> Full logbook

**bot** `M·ask:panel_paint_condition`
> Good to know it's full logbook. how would you rate the panel and paint condition - excellent, good, fair, or poor?

**seller**
> Good condition

**bot** `M·ask:mechanical_faults`
> good to hear the panels are in decent shape. any mechanical faults or warning lights showing on the dash? if none, just say none.

**seller**
> None

**bot** `M·ask:tyre_condition`
> good to hear there's nothing mechanical going on. how are the tyres — new, good, worn, or need replacing?

**seller**
> Tyres are good

**bot** `M·ask:keys_count`
> good to know the tyres are in decent shape. how many keys come with the car?

**seller**
> Two keys

**bot** `M·ask:seller_phone`
> two keys, good. what's the best mobile number for you? messenger only lets us reply for a day after your last message, and i don't want the offer to have no way of reaching you.

**seller**
> 0412 345 678

**bot** `M·ask:photos`
> got it, thanks. last thing for now - can you send photos: the six exterior angles (front, back, both sides, both front corners), the interior, and the dash with the ignition on so the odometer shows?

**seller**
> Here you go

**bot** `M·verification_wait`
> thanks Mei, that covers everything we need on the discovery side. we'll run the PPSR and rego checks now and come back with a firm number shortly.

**bot** `M·offer`
> here's where we've landed: $5,600 for the Outlander, subject to inspection. this offer is open until 3:19pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Too low mate

**bot** `M·concession`
> fair enough, mate — here's the improved number: $5,950, subject to inspection. that offer's open until 3:19pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Come on, you can do better than that

**bot** `M·concession`
> hear you, mei — here's the improved number: $6,200, subject to inspection. it's not my call beyond this, but that's the best i can put forward. this offer is open until 3:20pm on Wednesday 23 September, after which the valuation inputs move and it lapses. want to go ahead?

**seller**
> Still not enough

**bot** `M·at_ceiling`
> understood, mei. $6,200 is where the number stands from my side — going any higher isn't something I can decide. I'll flag your feedback and Alex will come back to you on it.

Offers: $5,600 (opening, superseded) → $5,950 (step_1, superseded) → $6,200 (step_2, open)

Human queue: verification_review, ceiling_approval

</details>

---

Tokens: 653,997 in, 24,071 out. Prices are the published Sep 2026 rates — check your own plan.