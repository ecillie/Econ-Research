import pandas as pd
from flights_pipeline import flight_pipeline
from identify_exits import match_flights_exit
from assing_carrier_catagory import add_carrier_type

def final_pipeline() -> pd.DataFrame:
    flights = flight_pipeline()
    flights = match_flights_exit(flights)
    flights = add_carrier_type(flights)
    return flights


