# SPDX-License-Identifier: GPL-3.0-or-later
"""The worm box as a living thing, and the sensors that watch it. Plain Python: no ROS, no Webots.

A worm box turns kitchen scraps into castings, a liquid and a little heat.
How fast depends on how the worms are doing. This is a small model of that,
good enough to try rules and a feeder against. Every number is a rule of
thumb from composting practice, not a measurement of a real box.

WHAT IS IN THE BOX (grams, wet):  food not yet eaten, bedding, castings, worms.

EACH SIMULATED MINUTE
  activity   0..1: how hard the worms work. Best at 15-25 °C in the core and
             65-85 % moisture; nothing under 4 °C or over 32 °C; less without air.
  eating     worms eat up to half their own weight a day, times activity and
             health. Of what is eaten, 40 % stays as castings, 30 % drains
             as liquid into the SUMP (the lower bin), 30 % leaves as gas and
             vapour. So the
             box LOSES 0.6 g for every gram eaten: that is what load cells see.
  heat       food that lies uneaten composts and warms the core (about
             2.5 °C per kg). The edge follows the air, slowly; wrapped in
             insulation, five times more slowly.
  air        much uneaten food, or a soaked box, uses the air up. An airless
             box smells (the gas sensor) and harms the worms.
  kinds      food differs (the feeding guide's numbers): how wet it is, how
             much it heats when it lies uneaten, how sour it makes the box.
             Acid builds up from citrus and the like and is buffered away
             over days, faster with eggshell and bedding. A sour box (the pH
             probe) harms the worms.
  sump       the lower bin catches the liquid, and rain that falls through
             an open lid. Full, it backs up and soaks the working bin. A tap
             lets it on to the tank (`drain`).
  harvest    the box has two halves. When one is RESTED (no food goes there)
             the worms move to the fed half; after about ten days the
             resting half's castings are finished and can be scooped out, a
             little at a time. When they are gone the halves swap roles.
  health     0..1. Frost, heat, drought, a soaked or airless box wear it
             down. When worms may leave into the soil (the guild's setting)
             they dodge half the harm and come back when it is good again; in
             a closed box what is lost stays lost.
  population red wigglers (Eisenia fetida) by life stage: cocoons, young and
             adults, each kept in cohorts (one a day) that gather degree-days.
             Adults lay cocoons by temperature, food, moisture and crowding;
             cocoons hatch, the young grow up, and all of them die of age,
             of hard conditions and of hunger. Worms that may leave go into
             the bed when the box is bad, live there a while under a mulch,
             and come back. `worms_g` is the sum, so eating follows it.
             The numbers and where they come from are in the table below.

THE SENSORS  (`Sensors.read`) give what real ones would: the value plus a
little noise; None from a dead one; the old value from a stuck one.

    box = WormBox(may_leave=True)
    box.feed(300)
    out = box.step(minutes=1.0, air_c=16.0)      # -> {"liquidL": ..., "eatenG": ...}
"""
import math
import random

EATS_OWN_WEIGHT_PER_DAY = 0.5
TO_CASTINGS, TO_LIQUID, TO_AIR = 0.4, 0.3, 0.3
LOSS_PER_G_EATEN = TO_LIQUID + TO_AIR          # what the load cells see for every gram eaten
HEAT_C_PER_KG_FOOD = 2.5
EDGE_LAG_MIN, CORE_LAG_MIN = 180.0, 60.0
INSULATED_LAG_X = 5.0
EDGE_ABOVE_AIR_C = 1.0
FOOD_WETS_PER_G = 1.0 / 200.0                  # percent of moisture a gram of scraps adds
BEDDING_DRIES_PER_G = 1.0 / 60.0               # percent of moisture a gram of dry paper takes up
DRIES_PER_MIN, DRIES_OPEN_PER_MIN = 0.002, 0.01
FOOD_AIRLESS_G = (1500.0, 4000.0)              # uneaten food: air starts to run short, is gone
WET_AIRLESS_PCT = (85.0, 95.0)
FULL_G = 20000.0
READY_AFTER_MIN = 10 * 1440.0                  # a rested half is finished after about ten days
ACID_BUFFER_MIN = 5 * 1440.0                   # acid fades over about five days
BEDDING_BUFFERS_PER_KG = 0.3
BACKS_UP_PCT_PER_L = 4.0                       # percent of moisture a litre the sump cannot take adds to the bin
MINUTES_PER_DAY = 1440.0

# -- the worm population (Eisenia fetida) --------------------------------------------------
# From the literature, rounded; "to confirm" against a real box:
#   laying       one cocoon every second day at 25 °C, rising linearly from 10 °C
#                (Reinecke & Kriel 1981)
#   hatching     2.7 young per cocoon after about 23 days at 20-25 °C; slow when cold
#                (life-cycle study on cattle manure; Reinecke & Kriel 1981)
#   maturity     40-60 days in good conditions (same study)
#   crowding     growth and maturing slow with density (Domínguez & Edwards 1997);
#                vermiculture stocks 1-2 kg of worms per m²
# Rules of thumb, not from a study: the 80 % viability, the masses, the deaths,
# the hunger and the life of worms that went into the bed.
LAY_PER_DAY = 0.5                    # cocoons per adult a day at best
HATCH_DD = 333.0                     # degree-days above BASE_C: 23 days at 22.5 °C
MATURE_DD = 650.0                    # degree-days above BASE_C for a hatchling to be adult: 45 days at 22.5 °C
BASE_C = 8.0
YOUNG_PER_COCOON, VIABLE = 2.7, 0.8
HATCHLING_G, ADULT_G = 0.01, 0.45
CROWDED_KG_M2 = (2.0, 5.0)           # laying and growing up slow from the first to nothing at the second
AGE_DEATH_PER_DAY = math.log(2) / 365.0   # half the adults of a working box gone in a year
YOUNG_DEATH_PER_DAY = math.log(2) / 30.0     # most hatchlings never grow up
HUNGER_DEATH_PER_DAY = 0.02          # adults, when the food has been gone for days (they live on bedding a while); the young twice that
HUNGER_LEAVE_PER_DAY = 0.02          # hungry worms that may leave go looking in the bed
NOURISH_DAYS = 3.0                   # how slowly being fed or going hungry builds up
STRESS_DEATH_PER_DAY = 1.0           # at stress 1, most die within a day
LEAVE_PER_DAY = 1.0                  # at stress 1, worms that may leave go into the bed this fast
BED_LIFE_DAYS = 14.0                 # worms in the bed without a mulch; each cm of mulch adds as much again
CROWDED_YOUNG_DEATH_PER_DAY = 0.08    # extra deaths of the young in a full box: the brake that holds the population
RETURN_PER_DAY = 0.2                 # back into the box once it is good again


def ramp(x, lo, hi):
    """0 at lo, 1 at hi, straight between (lo may be above hi)."""
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def temp_factor(c):
    """How hard worms work at this core temperature, 0..1."""
    return min(ramp(c, 4.0, 15.0), ramp(c, 32.0, 25.0))


def moisture_factor(pct):
    return min(ramp(pct, 40.0, 65.0), ramp(pct, 95.0, 85.0))


def lay_temp_factor(c):
    """Cocoons a worm lays at this core temperature, as a share of the best: linear from 10 to 25 °C."""
    return min(ramp(c, 10.0, 25.0), ramp(c, 32.0, 27.0))


def degree_days(c, days):
    return max(0.0, min(c, 30.0) - BASE_C) * days


def young_g(progress):
    """A young worm's mass, by how far it has grown up (0: hatched, 1: adult)."""
    return HATCHLING_G + (ADULT_G - HATCHLING_G) * max(0.0, min(1.0, progress))


class WormBox:
    def __init__(self, may_leave=True, air_c=16.0, worms_g=800.0, food_g=400.0, bedding_g=4000.0, castings_g=1000.0,
                 sump_cap_l=14.0, area_m2=0.48, mulch_cm=0.0):
        self.may_leave, self.mulch_cm = may_leave, float(mulch_cm or 0.0)
        self.sump_l, self.sump_cap_l, self.area_m2 = 0.0, sump_cap_l, area_m2
        self.food_g, self.bedding_g, self.castings_g = food_g, bedding_g, castings_g
        # a settled box: most of the mass in adults, some young half grown, a few weeks of cocoons
        self.adults = 0.85 * worms_g / ADULT_G
        self.young = [[0.15 * worms_g / young_g(0.5), 0.5 * MATURE_DD]]     # cohorts: [count, degree-days]
        self.cocoons = [[self.adults * 0.3, dd] for dd in (220.0, 110.0, 0.0)]   # cohorts, oldest first
        self.in_bed = 0.0                # worms that left the box into the bed
        self.nourish = 1.0               # 0: starving for days; 1: well fed
        self.laid = self.hatched = self.matured = self.died = 0.0
        self.day_n = 0.0                 # simulated days, to start one cohort a day
        self.lay_day = None
        self.heat_g = food_g             # the uneaten food, weighted by how strongly its kinds heat
        self.moisture = 70.0
        self.edge_c = air_c + EDGE_ABOVE_AIR_C
        self.core_c = self.edge_c + self.heat_c()
        self.air = 1.0                   # 1: fresh, 0: airless
        self.health = 1.0
        self.insulated = False
        self.lid_open = False
        self.acid = 0.3                  # 0: neutral (pH 7); 3: sour (pH 5.5)
        self.rest_side = None            # the half that is rested for harvest: 0 or 1; None: both are fed
        self.rest_min = 0.0
        self.next_side = 1               # the half to rest at the next harvest: they take turns
        self.ready_g = 0.0               # finished castings that can be scooped from the resting half
        self.taken_g = 0.0               # castings taken out, in all
        self.fed_g = 0.0                 # what the robot dropped in, in all
        self.feedings = 0
        self.eaten_g = 0.0

    # -- what a robot or a person does ---------------------------------------------

    def feed(self, grams, by_robot=True, wet=1.0, heat=1.0, acid=0.0):
        """Food goes in. `wet`, `heat` and `acid` are the feeding guide's numbers for what it is (1, 1, 0: plain vegetable scraps)."""
        grams = max(0.0, min(grams, FULL_G - self.weight_g()))
        self.food_g += grams
        self.heat_g += grams * heat
        self.acid = max(0.0, self.acid + grams / 1000.0 * acid)
        self.moisture = min(98.0, self.moisture + grams * FOOD_WETS_PER_G * wet)
        if by_robot:
            self.fed_g += grams
            self.feedings += 1
        return grams

    def add_bedding(self, grams):
        """Dry shredded paper: takes up moisture and lets air in."""
        grams = max(0.0, min(grams, FULL_G - self.weight_g()))
        self.bedding_g += grams
        self.moisture = max(40.0, self.moisture - grams * BEDDING_DRIES_PER_G)
        self.air = min(1.0, self.air + grams / 1000.0)
        self.acid = max(0.0, self.acid - grams / 1000.0 * BEDDING_BUFFERS_PER_KG)

    def moisten(self, ml):
        self.moisture = min(98.0, self.moisture + max(0.0, ml) / 100.0)

    def rest(self, side):
        """Stop feeding one half (0 or 1) so the worms leave it; None feeds both again."""
        self.rest_side = None if side is None else int(side) % 2
        self.rest_min, self.ready_g = 0.0, 0.0

    def ready(self):
        return self.rest_side is not None and self.rest_min >= READY_AFTER_MIN

    def take_castings(self, grams):
        """Scoop finished castings from the resting half. Returns what came out; when the half is empty it
        becomes the fed half and the other can be rested next."""
        out = max(0.0, min(grams, self.ready_g)) if self.ready() else 0.0
        self.ready_g -= out
        self.castings_g -= out
        self.taken_g += out
        if self.ready() and self.ready_g < 25.0:
            self.next_side = 1 - self.rest_side
            self.rest(None)
        return out

    def harvest(self):
        """What the robot knows about the harvest: which half rests, for how long, and whether it is ready."""
        return {"side": self.rest_side, "restDays": round(self.rest_min / MINUTES_PER_DAY, 1), "ready": self.ready(),
                "readyAfterDays": READY_AFTER_MIN / MINUTES_PER_DAY, "nextSide": self.next_side}

    def drain(self, room_l=1e9):
        """Open the tap: the sump's liquid runs on (to the tank), as far as there is room. Returns the litres."""
        out = max(0.0, min(self.sump_l, room_l))
        self.sump_l -= out
        return out

    def take_out(self, grams):
        """A person takes uneaten food out again."""
        out = max(0.0, min(grams, self.food_g))
        if self.food_g > 0:
            self.heat_g *= (self.food_g - out) / self.food_g
        self.food_g -= out

    # -- readings of the truth -------------------------------------------------------

    @property
    def worms_g(self):
        """The worms in the box, by mass: adults and the young as far as they have grown."""
        return self.adults * ADULT_G + sum(n * young_g(dd / MATURE_DD) for n, dd in self.young)

    def young_n(self):
        return sum(n for n, _ in self.young)

    def cocoons_n(self):
        return sum(n for n, _ in self.cocoons)

    def density_kg_m2(self):
        return self.worms_g / 1000.0 / self.area_m2

    def crowding(self):
        """1: room enough; 0: so crowded that no cocoons are laid and the young stop growing."""
        return ramp(self.density_kg_m2(), CROWDED_KG_M2[1], CROWDED_KG_M2[0])

    def weight_g(self):
        return self.worms_g + self.food_g + self.bedding_g + self.castings_g

    def heat_c(self):
        return HEAT_C_PER_KG_FOOD * self.heat_g / 1000.0

    def activity(self):
        return temp_factor(self.core_c) * moisture_factor(self.moisture) * (0.3 + 0.7 * self.air)

    def eats_per_day(self):
        """Grams a day at this moment, if there is that much food."""
        return self.worms_g * EATS_OWN_WEIGHT_PER_DAY * self.activity() * self.health

    def stress(self):
        """0: fine. 1: conditions that kill within about a day."""
        c, m = self.core_c, self.moisture
        return max(ramp(c, 5.0, 0.0), ramp(c, 30.0, 35.0), ramp(m, 45.0, 35.0), ramp(m, 92.0, 98.0), ramp(self.air, 0.4, 0.0),
                   ramp(self.acid, 2.5, 5.5))

    def ph(self):
        return 7.0 - 0.5 * self.acid

    def gas(self):
        """The smell a gas sensor reads, 0..100: 0 fresh, 100 airless and rotting."""
        return 100.0 * (1.0 - self.air)

    # -- one step ----------------------------------------------------------------------

    def step(self, minutes, air_c, rain_mm_h=0.0):
        if minutes <= 0:
            return {"liquidL": 0.0, "eatenG": 0.0}
        open_x = 3.0 if self.lid_open else 1.0
        lag = EDGE_LAG_MIN * (INSULATED_LAG_X if self.insulated else 1.0) / open_x
        self.edge_c += (air_c + EDGE_ABOVE_AIR_C - self.edge_c) * (1.0 - math.exp(-minutes / lag))
        self.core_c += (self.edge_c + self.heat_c() - self.core_c) * (1.0 - math.exp(-minutes / CORE_LAG_MIN))

        self.moisture = max(30.0, self.moisture - (DRIES_OPEN_PER_MIN if self.lid_open else DRIES_PER_MIN) * minutes)
        rain_l = 0.0
        if self.lid_open and rain_mm_h > 0:                        # rain through an open lid wets the bin and runs through it
            self.moisture = min(98.0, self.moisture + rain_mm_h * minutes / 60.0)
            rain_l = rain_mm_h * minutes / 60.0 * self.area_m2     # 1 mm on 1 m² is a litre

        wants = 1.0 - max(ramp(self.food_g, *FOOD_AIRLESS_G), ramp(self.moisture, *WET_AIRLESS_PCT))
        self.air += (wants - self.air) * (1.0 - math.exp(-minutes / 240.0))

        appetite = self.eats_per_day() * minutes / MINUTES_PER_DAY
        eaten = min(self.food_g, appetite)
        if self.food_g > 0:
            self.heat_g *= (self.food_g - eaten) / self.food_g
        self.acid *= math.exp(-minutes / ACID_BUFFER_MIN)          # castings and bedding buffer it away
        self.food_g -= eaten
        self.castings_g += eaten * TO_CASTINGS
        self.eaten_g += eaten

        days = minutes / MINUTES_PER_DAY
        harm = self.stress() * (0.5 if self.may_leave else 1.0)
        if harm > 0:
            self.health = max(0.0, self.health - harm * days)
        elif self.may_leave:
            self.health = min(1.0, self.health + 0.2 * days)       # they come back from the soil
        self.live(days, air_c, eaten / appetite if appetite > 0 else None)
        if self.rest_side is not None:
            was = self.ready()
            self.rest_min += minutes
            if self.ready() and not was:                           # the worms have left: that half's castings are finished
                self.ready_g = self.castings_g * 0.5
        liquid = eaten * TO_LIQUID / 1000.0
        self.sump_l += liquid + rain_l
        if self.sump_l > self.sump_cap_l:                          # a full sump backs up into the working bin
            self.moisture = min(98.0, self.moisture + (self.sump_l - self.sump_cap_l) * BACKS_UP_PCT_PER_L)
            self.sump_l = self.sump_cap_l
        return {"liquidL": liquid, "eatenG": eaten}

    def live(self, days, air_c, fed=None):
        """The population over `days`: laying, hatching, growing up, dying, leaving and coming back. `fed`: what
        they ate as a share of what they would have eaten (None when they ate nothing because they could not)."""
        c, crowd = self.core_c, self.crowding()
        if fed is not None:                                           # a box too cold to eat in does not make them hungry
            self.nourish += (fed - self.nourish) * (1.0 - math.exp(-days / NOURISH_DAYS))
        hunger = ramp(self.nourish, 0.3, 0.05)
        stress = self.stress()

        # cocoons: laid into today's cohort; they gather warmth and hatch
        lay = self.adults * LAY_PER_DAY * lay_temp_factor(c) * moisture_factor(self.moisture) * self.nourish * crowd * self.health * days
        day = int(self.day_n)
        self.day_n += days
        if lay > 0:
            if self.cocoons and self.lay_day == day:
                self.cocoons[-1][0] += lay
            else:
                self.cocoons.append([lay, 0.0])
                self.lay_day = day
            self.laid += lay
        warmth = degree_days(c, days)
        hatched = 0.0
        if c > 35.0:                                                  # cocoons cook in a hot box
            for co in self.cocoons:
                co[0] *= math.exp(-days)
        for co in self.cocoons:
            co[1] += warmth
        while self.cocoons and self.cocoons[0][1] >= HATCH_DD:
            hatched += self.cocoons.pop(0)[0] * YOUNG_PER_COCOON * VIABLE
        if hatched > 0:
            self.young.insert(0, [hatched, 0.0])
            self.hatched += hatched

        # the young grow with warmth and food, slower when crowded; the oldest cohort becomes adult
        grow = warmth * max(0.2, self.nourish) * max(0.3, crowd)
        matured = 0.0
        for co in self.young:
            co[1] += grow
        while self.young and self.young[-1][1] >= MATURE_DD:
            matured += self.young.pop()[0]
        self.adults += matured
        self.matured += matured
        if len(self.young) > 120:                                     # merge the youngest pairs to keep the list short
            a, b = self.young[0], self.young.pop(1)
            a[1] = (a[0] * a[1] + b[0] * b[1]) / max(1e-9, a[0] + b[0])
            a[0] += b[0]

        # dying, and with leave to go: leaving instead of part of it
        harm = stress * STRESS_DEATH_PER_DAY * (0.5 if self.may_leave else 1.0)
        die_adult = 1.0 - math.exp(-(AGE_DEATH_PER_DAY + harm + hunger * HUNGER_DEATH_PER_DAY) * days)
        die_young = 1.0 - math.exp(-(YOUNG_DEATH_PER_DAY + (1.0 - crowd) * CROWDED_YOUNG_DEATH_PER_DAY + harm
                                     + 2.0 * hunger * HUNGER_DEATH_PER_DAY) * days)
        died = self.adults * die_adult
        self.adults -= died
        for co in self.young:
            died += co[0] * die_young
            co[0] *= 1.0 - die_young
        self.young = [co for co in self.young if co[0] > 0.01]
        self.died += died
        if self.may_leave:
            go = 1.0 - math.exp(-(stress * LEAVE_PER_DAY + hunger * HUNGER_LEAVE_PER_DAY) * days)
            left = self.adults * go
            self.adults -= left
            self.in_bed += left
            bed_life = BED_LIFE_DAYS * (1.0 + self.mulch_cm) * (0.3 if air_c < -2.0 else 1.0)
            self.in_bed *= math.exp(-days / bed_life)
            if stress == 0.0 and hunger == 0.0:
                back = self.in_bed * (1.0 - math.exp(-RETURN_PER_DAY * days))
                self.in_bed -= back
                self.adults += back

    def population(self):
        """What only a simulation can count: the worms by stage, and how crowded the box is."""
        return {"cocoons": round(self.cocoons_n()), "young": round(self.young_n()), "adults": round(self.adults),
                "inBed": round(self.in_bed), "wormsG": round(self.worms_g), "kgPerM2": round(self.density_kg_m2(), 2),
                "crowding": round(1.0 - self.crowding(), 2), "nourish": round(self.nourish, 2),
                "laid": round(self.laid), "hatched": round(self.hatched), "matured": round(self.matured), "died": round(self.died)}

    def truth(self):
        """What is really going on: only a simulation can show this, a real box cannot."""
        return {"activity": round(self.activity(), 2), "health": round(self.health, 2), "wormsG": round(self.worms_g),
                "foodG": round(self.food_g), "castingsG": round(self.castings_g), "eatsPerDay": round(self.eats_per_day()),
                "eatenG": round(self.eaten_g), "air": round(self.air, 2), "acid": round(self.acid, 2), "readyG": round(self.ready_g),
                "population": self.population()}


NOISE = {"wormBin.pH": 0.03, "wormBin.sumpL": 0.03, "wormBin.tempC": 0.1, "wormBin.edgeC": 0.1, "wormBin.moisture": 0.3, "wormBin.weightKg": 0.015, "wormBin.gas": 1.0}
DIGITS = {"wormBin.weightKg": 3, "wormBin.sumpL": 2, "wormBin.pH": 2}
FLOOR = {"wormBin.gas": 0.0, "wormBin.sumpL": 0.0}


class Sensors:
    """The box's fixed sensors: two temperature probes (core, edge), moisture, load cells, gas, the sump's level, acidity (pH), a lid switch."""

    def __init__(self, seed=5):
        self.rng = random.Random(seed * 7919 + 1)
        self.frozen = {}

    def one(self, path, value, mode=None):
        if mode == "dead":
            return None
        if mode == "stuck" and path in self.frozen:
            return self.frozen[path]
        value = round(max(FLOOR.get(path, -1e9), value + self.rng.gauss(0.0, NOISE.get(path, 0.0))), DIGITS.get(path, 1))
        if mode == "stuck":
            self.frozen[path] = value
        else:
            self.frozen.pop(path, None)
        return value

    def read(self, box, mode_of=lambda path: None):
        """`mode_of(path)` -> None | "dead" | "stuck" (environment.Environment.sensor)."""
        s = lambda key, v: self.one(f"wormBin.{key}", v, mode_of(f"wormBin.{key}"))   # noqa: E731
        return {"tempC": s("tempC", box.core_c), "edgeC": s("edgeC", box.edge_c), "moisture": s("moisture", box.moisture),
                "weightKg": s("weightKg", box.weight_g() / 1000.0), "gas": s("gas", max(0.0, box.gas())), "sumpL": s("sumpL", box.sump_l), "pH": s("pH", box.ph()),
                "lidOpen": box.lid_open}
