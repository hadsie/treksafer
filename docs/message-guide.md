# Reading TrekSafer Messages

This guide explains everything TrekSafer might send you and how to read it. Outside of just getting the raw data communicated we've focussed on two issues:

- **Satellite messages are expensive and slow.** Many plans charge per message, so TrekSafer puts as much as it can into as few texts as possible.
- **Satellite messages can arrive out of order.** Because of this, we don't split a single fire across two texts messages. This might mean additional texts, or abbreviated messages to squeeze a lot of content into the character limit of SMS.

## TL;DR

- Fires are listed in order of relevancy. Distance matters most, but a large uncontrolled fire a little farther away can outrank a small contained one nearby.
- The nearest and most serious fires get full detail. Farther ones get shorter formats, possibly down to a single line for small fires far away.
- If there are more fires than fit the maximum SMS limit, the reply ends with a `+N` line naming what was left out, so nothing is hidden from you.
- Air quality and wind lines only appear when relevant. No line means nothing notable, not "no data".

## Standard fire reply

```
AQI: 95
Wind: 12km/h from SW rising to 30

Fire: Lower Young Creek (K50911)
Location: 7 km NE of Lillooet
25km E
Size: 66 ha (+20 since 26h ago)
Status: Out of Control
```

Reading it line by line:

- **AQI: 95** is the air quality index at your location right now. Learn more about AQI [here](https://www.airnow.gov/aqi/aqi-basics).
- **Wind: 12km/h from SW rising to 30** is the sustained wind. Direction is where the wind blows *from*, so a SW wind could potentially push fire and smoke toward the NE. "Rising to 30" is a forecast: the strongest wind expected in the next several hours. Both AQI and wind forecast data are sourced from [Open-Meteo](https://open-meteo.com/)
- **Fire:** the fire's name, with its official fire number in parentheses. The fire number can also be used to directly request [more information about that fire](#watching-one-fire).
- **Location:** the fire's locality. This field varies significantly for each provider.
- **25km E** is the straight-line distance and compass direction from the coordinates you sent to the nearest edge of the fire.
- **Size: 66 ha (+20 since 26h ago)** is the fire's size in hectares. The
  part in parentheses shows recent growth: this fire gained 20 hectares in
  the last 26 hours. A minus number means it shrank.
- **Status: Out of Control** is the fire agency's own assessment. See the
  status list below.

## Why fires appear in that order

Fires are sorted by relevance, not strictly by distance. Distance matters
most, but a fire's status and size adjust its place: a 300-hectare
out-of-control fire 18 km away is more important to you than a
1-hectare fire that crews already contained 10 km away, so it is listed
first and gets more detail. A distant fire never outranks one genuinely
close to you.

## Three levels of detail

To keep replies short, fires shrink in format the further down the list
they are. The most relevant fires look like the full example above. After
that comes a three-line version:

```
Fire: Skagit Bluffs K50921
24km W BH
Size: 1.5 ha
```

and finally a one-line version:

```
F1032 35ha(+10) 35km SSE OC
```

That single line reads: fire F1032, 35 hectares (grew 10 recently), 35 km
to your south-southeast, Out of Control. The status is always kept in the
one-line format, abbreviated:

| Code | Meaning |
|------|---------|
| OC | Out of Control |
| FN | Fire of Note |
| NUC | Not Under Control |
| BO | Being Observed |
| BH | Being Held |
| UC | Under Control |
| Out | Out (extinguished) |

The first four all mean the fire is active and uncontained. "Being Held"
means it is not expected to spread under current conditions. "Under
Control" means it is contained. Some US fires report a containment
percentage instead (for example "45% contained"), which is passed along
as-is.

## When there are more fires than fit

A reply is capped at a few text messages. If more fires matched your
request than fit, the reply ends with a line like:

```
+2 40-50km: K50921 V33019
```

meaning two more fires, between 40 and 50 km away, with their fire
numbers. If even the list of numbers is too long for a text, you get just
the count:

```
+6 more 40-60km
```

Nothing is ever silently dropped: every fire in your search area is either
shown or counted on this line. To see them in detail, look them up by
number (`fireid K50921 V33019`). To see fewer, closer fires, send a smaller
radius (for example `fires 25km`).

To get every fire at full detail with nothing left out, add `!full` to
your message (for example `fires !full`). Expect a longer reply, and more
text messages, in a busy fire area.

## Air quality and wind

These lines only appear when they carry real information:

- **AQI** shows when air quality is already poor, or when it is forecast
  to get much worse within the next few hours: `AQI: 35 rising to 160`
  means it is fine now but heavy smoke is expected. "Rising to" always
  refers to a forecast, never a current measurement.
- **Wind** shows when sustained winds are strong enough to drive fire
  behavior, and only when there are fires in your report (wind with
  nothing burning nearby is not fire information).

On a clear, calm day you get neither line. That means conditions are
unremarkable, not that the data was missing.

## New fires

A fire discovered within the last week is labelled `(NEW)` after its name.
New fires often have no size estimate yet, shown as `Size: Unknown`.
A brand-new fire near you is worth attention regardless of its size: every
big fire was small once.

## How fresh is the information

TrekSafer reads each fire agency's live feed when you ask. Two markers
tell you when what you are reading is older than that:

- **As of 3h ago** (on single-fire lookups): the last time the agency
  itself updated this fire's record.
- **Data from Aug 7 18:00** (at the end of a report): the agency's feed
  could not be reached, so this reply was built from the most recent data
  TrekSafer has stored. Treat it as a snapshot from that time.

No marker means the reply reflects the agency's current feed. Keep in mind
agencies update on their own schedules; "current feed" can still lag the
fire on the ground.

## Watching one fire

Send `fireid` and a fire number to follow a specific fire:

```
fireid K50911 (50.68, -121.93)
```

Include your coordinates and the reply adds distance and direction from
you. To look up several fires at once, list their numbers:
`fireid K50921 V33019`. A lookup reply can include additional data:

```
Fire: Lower Young Creek (K50911)
25km E
Size: 66 ha (+20 since 26h ago)
Status: Out of Control
Perim: 50.62-50.71N 121.55-121.44W
Edge: moved ~2km NE in the last 26h, was 27km from you
As of 3h ago
```

- **Perim:** the box the fire's mapped perimeter fits inside, as
  latitude and longitude ranges. Useful for plotting on a map.
- **Edge:** how far the mapped fire edge advanced since the last distinct
  mapping, in which direction, and how far that edge was from you before.
  In this example the fire has moved toward you.

If a looked-up fire is from a past season and long dead, TrekSafer replies
that no fire was found rather than showing you stale history. A fire from
recent months, or an older fire that is genuinely still burning, is shown
with an honest "As of" age.

## When you get no fires

- **"No fires reported within 50km of your location (49.06, -120.79)."**
  The area is clear for the statuses searched. Your coordinates are echoed
  back so you can confirm TrekSafer understood your position correctly;
  always glance at them. By default extinguished fires are excluded, and
  the message says which statuses were included.
- **"GPS coordinates outside of supported fire perimeter area."** Your
  location is outside the regions TrekSafer has data for (Canada and the
  US).
- **"Fire data is temporarily unavailable for your area."** The agency's
  feed is down and TrekSafer has nothing stored for that source. This is
  never shown as "no fires": no data and no fires are different answers.

## Avalanche replies

In winter, if your location is covered by an avalanche forecast provider
(Avalanche Canada, Avalanche Quebec, US National Avalanche Center), you
get the avalanche forecast instead of a fire report:

```
Spearhead
Fri: ALP:C TL:M BTL:M
Sat: ALP:C TL:M BTL:M

StormSlb
AllElev Slp:All
Lkly, Sz:1-2.5

PrsistSlb
ALP Slp:N,NE,E
UnLkly/Poss, Sz:2.5-3.5
```

- The first message is the forecast region, then one line per forecast day
  with the danger rating for each elevation band: **ALP** (alpine),
  **TL** (treeline), **BTL** (below treeline), rated **L**ow,
  **M**oderate, **C**onsiderable, **H**igh, or **E**xtreme. The first
  line of the example reads: on Friday, Considerable danger in the alpine,
  Moderate at treeline and below.
- Each following message is one avalanche problem:
  - The problem type, abbreviated: `StormSlb` (storm slab), `WindSlb`
    (wind slab), `PrsistSlb` (persistent slab), `DeepPerSlb` (deep
    persistent slab), `WetSlb` (wet slab), `Cornice`, `LooseWet`,
    `LooseDry`.
  - The elevation bands and slope aspects it affects: `ALP Slp:N,NE,E`
    means alpine elevations on north through east-facing slopes;
    `AllElev Slp:All` means everywhere.
  - How likely an avalanche is (`UnLkly`, `Poss`, `Lkly`, `VLkly`,
    `Certain`; a pair like `UnLkly/Poss` means between the two) and the
    expected size (`Sz:1-2.5`, on the 1 to 5 avalanche size scale, where
    2 can bury a person and 3 can destroy a small building).

Like fires, each problem arrives as its own self-contained message.

## Odd cases worth knowing about

- **Messages arrive out of order.** Normal on satellite. Every message
  stands alone, so read them in any order.
- **You typed coordinates and your device attached its own.** Coordinates
  you type always win, so you can ask about somewhere you are not (a
  planned camp, a friend's route). The reply echoes the coordinates it
  used; check them.
- **A fire with no size.** Agencies sometimes have no estimate yet,
  especially for new fires. The fire still shows, with `Size: Unknown` in
  the full format and no size in the shorter ones.
- **The same fire number in different years.** Some agencies reuse fire
  numbers each season. Lookups always resolve to the current fire when
  one exists.
- **Filters you can send:** `active` (uncontained fires only), `all`
  (everything, including extinguished), a radius like `25km` or `10mi`
  (up to 150 km). The coordinate formats TrekSafer understands are listed
  in the [README](../README.md).
