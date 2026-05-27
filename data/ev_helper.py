from .ev_database import EV_DATABASE

AC_USAGE_VALUES = ("always", "sometimes", "never")


AC_MULTIPLIERS = {
    "always":    0.85,
    "sometimes": 0.92,
    "never":     1.00,
}

def get_car_specs(model_name: str) -> dict:
    """
    Return full specs dict for a car model.
    Raises ValueError if model is not found.
    """
    if model_name not in EV_DATABASE:
        raise ValueError(
            f"Car model '{model_name}' not found in EV database. "
            f"Use get_all_models() to see available models."
        )
    return EV_DATABASE[model_name]


def get_all_models() -> list:
    """Return sorted list of all car model names."""
    return sorted(EV_DATABASE.keys())


def get_all_brands() -> list:
    """Return sorted list of unique brand names."""
    brands = {specs["brand"] for specs in EV_DATABASE.values()}
    return sorted(brands, key=lambda b: b.lower())


def get_models_by_brand(brand: str) -> list:
    """Return all models belonging to a specific brand."""
    return sorted([
        model for model, specs in EV_DATABASE.items()
        if specs.get("brand", "").lower() == brand.lower()
    ])


def get_ac_connector(model_name: str) -> str:
    """Return AC connector type (Type 2 or GB/T AC)."""
    return get_car_specs(model_name)["ac_connector"]


def get_dc_connector(model_name: str) -> str:
    """Return DC connector type (CCS2 or GB/T DC)."""
    return get_car_specs(model_name)["dc_connector"]


def is_station_compatible(
    model_name: str,
    station_connector: str,
    station_power_kw: float
) -> bool:
    """
    Check if a station is compatible with the car.
    DC fast charger: station power > 22 kW
    AC slow charger: station power <= 22 kW
    """
    specs = get_car_specs(model_name)

    if station_power_kw > 22:
        car_dc = specs["dc_connector"]
        if car_dc == "CCS2":
            return station_connector in ["CCS2", "CCS", "CCS (Type 2)"]
        if car_dc == "GB/T DC":
            return station_connector in ["GB/T DC", "GB/T"]
        return False

    else:
        car_ac = specs["ac_connector"]
        if car_ac == "Type 2":
            return station_connector in [
                "Type 2", "Type 2 (Socket Only)", "Schuko"
            ]
        if car_ac == "GB/T AC":
            return station_connector in ["GB/T AC", "GB/T", "Schuko"]
        return False

def can_reach_station(
    model_name: str,
    battery_level: int,
    station_distance_km: float,
    ac_usage: str = "sometimes",
    safety_buffer: float = 0.9
) -> bool:
    """
    Check if the car can physically reach a station.
    safety_buffer = 0.9 means station must be reachable
    with at least 10% range to spare.
    """
    if station_distance_km < 0:
        raise ValueError(
            f"station_distance_km cannot be negative, got {station_distance_km}"
        )
    range_remaining = get_range_remaining(
        model_name, battery_level, ac_usage
    )
    return station_distance_km <= (range_remaining * safety_buffer)


def calculate_target_charge(
    model_name: str,
    current_battery: int,
    distance_to_destination_km: float,
    ac_usage: str = "sometimes",
    min_battery_threshold: int = 10
) -> int:
    """
    Calculate the target charge percentage needed to complete the trip.

    Formula:
      energy_needed% = (distance / range) * 100
      target% = current% + energy_needed% + min_threshold%
      capped at 80% to avoid charging curve penalty

    Returns target battery percentage to charge to.
    """
    specs = get_car_specs(model_name)

    # Adjust range for AC usage
    effective_range = specs["range_km"] * AC_MULTIPLIERS.get(ac_usage, 0.92)

    # How much battery % is needed for the trip
    energy_needed_percent = (distance_to_destination_km / effective_range) * 100

    # Target = what we need for the trip + safety buffer
    target = current_battery + energy_needed_percent + min_battery_threshold

    # Never recommend charging above 80% — charging curve slows above this
    target = min(round(target), 80)

    # Never recommend below current battery
    target = max(target, current_battery + 1)

    return target


def is_fast_charge_compatible(
    model_name: str,
    station_power_kw: float
) -> bool:
    """
    Fast charging = station offers 50kW+ AND car DC max is 50kW+.
    """
    specs = get_car_specs(model_name)
    return station_power_kw >= 50 and specs["max_dc_kw"] >= 50


def get_effective_charge_speed(
    model_name: str,
    station_power_kw: float,
    is_dc_station: bool = True
) -> float:
    """
    what speed will the car actually charge at when plugged into this station?
    Get actual charging speed — limited by whichever is lower:
    the car's maximum acceptance rate or the station's output.
    """
    specs = get_car_specs(model_name)
    car_max = specs["max_dc_kw"] if is_dc_station else specs["max_ac_kw"]
    return min(car_max, station_power_kw)


def estimate_charge_time(
    model_name: str,
    current_battery: int,
    target_battery: int,
    station_power_kw: float,
    is_dc_station: bool = True
) -> int:
    """
    Estimate charging time in minutes from current_battery% to target_battery%.

    Two-phase model:
      Phase 1: current% → 80% at full effective speed
      Phase 2: 80% → target% at half speed (thermal protection above 80%)

    Returns 999 if effective speed is 0.
    """
    if not 0 <= current_battery <= 100:
        raise ValueError(
            f"current_battery must be 0-100, got {current_battery}"
        )
    if not 0 <= target_battery <= 100:
        raise ValueError(
            f"target_battery must be 0-100, got {target_battery}"
        )
    if current_battery >= target_battery:
        return 0

    specs = get_car_specs(model_name)
    battery_kwh = specs["battery_kwh"]

    effective_speed = get_effective_charge_speed(
        model_name, station_power_kw, is_dc_station
    )

    if effective_speed <= 0:
        return 999

    if target_battery <= 80:
        energy = ((target_battery - current_battery) / 100) * battery_kwh
        minutes = (energy / effective_speed) * 60

    else:
        if current_battery >= 80:
            # Already above 80% — only slow phase applies
            energy_above_80 = ((target_battery - current_battery) / 100) * battery_kwh
            minutes = (energy_above_80 / (effective_speed * 0.5)) * 60
        else:
            # Phase 1: current% → 80% at full speed
            energy_to_80 = ((80 - current_battery) / 100) * battery_kwh
            time_to_80   = (energy_to_80 / effective_speed) * 60
            # Phase 2: 80% → target% at half speed
            energy_above_80 = ((target_battery - 80) / 100) * battery_kwh
            time_above_80   = (energy_above_80 / (effective_speed * 0.5)) * 60
            minutes = time_to_80 + time_above_80

    return round(minutes)