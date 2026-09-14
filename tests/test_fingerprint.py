from acqbot.ingestion.fingerprint import odometer_matches, vehicle_fingerprint


def test_listing_text_variation_collapses():
    a = vehicle_fingerprint("Toyota", "Corolla", 2019)
    assert a == vehicle_fingerprint("TOYOTA ", "corolla hatch", 2019)
    assert a == vehicle_fingerprint("toyota", "Corolla Sedan", 2019)
    assert a != vehicle_fingerprint("Toyota", "Corolla", 2020)


def test_make_aliases():
    assert vehicle_fingerprint("VW", "Golf", 2018) == vehicle_fingerprint("Volkswagen", "Golf", 2018)
    assert vehicle_fingerprint("Mercedes-Benz", "C-Class", 2018) == vehicle_fingerprint(
        "Merc", "C Class", 2018
    )


def test_odometer_tolerance():
    assert odometer_matches(84_000, 86_500)
    assert odometer_matches(84_000, 92_000)  # within 10%
    assert not odometer_matches(84_000, 110_000)
    assert odometer_matches(1_000, 3_500)  # 3,000 km floor for low readings
