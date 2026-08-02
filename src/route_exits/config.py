"""Column definitions and carrier labels for monthly route-exit analysis."""

REQUIRED_COLUMNS = {
    "YEAR",
    "MONTH",
    "ORIGIN",
    "DEST",
    "UNIQUE_CARRIER",
    "DEPARTURES_PERFORMED",
    "SEATS",
    "PASSENGERS",
}
OPTIONAL_COLUMNS = {
    "UNIQUE_CARRIER_NAME",
    "ORIGIN_CITY_NAME",
    "DEST_CITY_NAME",
    "CLASS",
}
MARKETING_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
MARKETING_FORM_PARAMS = {"QO_fu146_anzr": "b0-gvzr", "gnoyr_VQ": "FGK"}
MARKETING_COLUMNS = {
    "YEAR",
    "MONTH",
    "MARKETING_AIRLINE_NETWORK",
    "OPERATING_AIRLINE",
    "ORIGIN",
    "DEST",
    "CANCELLED",
}
MARKETING_ALIASES = {
    "YEAR": ("YEAR",),
    "MONTH": ("MONTH",),
    "MARKETING_AIRLINE_NETWORK": (
        "MARKETING_AIRLINE_NETWORK",
        "MKT_UNIQUE_CARRIER",
    ),
    "OPERATING_AIRLINE": ("OPERATING_AIRLINE", "OP_UNIQUE_CARRIER"),
    "ORIGIN": ("ORIGIN",),
    "DEST": ("DEST",),
    "CANCELLED": ("CANCELLED",),
}
KNOWN_CARRIER_NAMES = {
    "9E": "Endeavor Air",
    "AA": "American Airlines",
    "AS": "Alaska Airlines",
    "B6": "JetBlue Airways",
    "C5": "CommuteAir",
    "DL": "Delta Air Lines",
    "F9": "Frontier Airlines",
    "G4": "Allegiant Air",
    "G7": "GoJet Airlines",
    "HA": "Hawaiian Airlines",
    "MQ": "Envoy Air",
    "MX": "Breeze Airways",
    "NK": "Spirit Airlines",
    "OH": "PSA Airlines",
    "OO": "SkyWest Airlines",
    "PT": "Piedmont Airlines",
    "QX": "Horizon Air",
    "UA": "United Airlines",
    "WN": "Southwest Airlines",
    "YV": "Mesa Airlines",
    "YX": "Republic Airways",
    "ZW": "Air Wisconsin",
}
