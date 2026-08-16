import pandas as pd


def add_carrier_type(flights: pd.DataFrame) -> pd.DataFrame:
    carrier_score = pd.read_csv(
        "/Users/evancillie/Documents/Econ Research/output/business_model_spectrum/spectrum_summary.csv",
        usecols=["carrier", "spectrum_score", "spectrum_side"],
    )

    carrier_score["carrier_type"] = carrier_score["spectrum_side"].map({
        "closer_to_lcc": "lcc",
        "partial_closer_to_lcc": "hybrid",
        "closer_to_fsnc": "legacy",
    })

    flights = flights.merge(
        carrier_score[
            ["carrier", "spectrum_score", "spectrum_side", "carrier_type"]
        ],
        on="carrier",
        how="left",
        validate="many_to_one",
    )

    flights["is_lcc"] = (flights["carrier_type"] == "lcc").astype("int8")
    flights["is_hybrid"] = (flights["carrier_type"] == "hybrid").astype("int8")
    flights["is_legacy"] = (flights["carrier_type"] == "legacy").astype("int8")

    return flights