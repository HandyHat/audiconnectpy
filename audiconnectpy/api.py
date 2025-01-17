"""Audi connect."""
from __future__ import annotations

import logging
from typing import Literal

from aiohttp import ClientSession

from .auth import Auth
from .exceptions import HttpRequestError, ServiceNotFoundError
from .models import Vehicle
from .services import AudiService
from .util import Globals

_LOGGER = logging.getLogger(__name__)

MAX_RESPONSE_ATTEMPTS = 10
REQUEST_STATUS_SLEEP = 5

ACTION_LOCK = "lock"
ACTION_CLIMATISATION = "climatisation"
ACTION_CHARGER = "charger"
ACTION_WINDOW_HEATING = "window_heating"
ACTION_PRE_HEATER = "pre_heater"


class AudiConnect:
    """Representation of an Audi Connect Account."""

    def __init__(
        self,
        session: ClientSession,
        username: str,
        password: str,
        country: str,
        spin: int,
        unit_system: str = "metric",
    ) -> None:
        """Initiliaze."""
        Globals(unit_system)
        self._audi_vehicles: list[Vehicle] = []
        self._auth = Auth(session)
        self._connect_delay = 10
        self._connect_retries = 3
        self._country = country
        self._excluded_refresh: set[str] = set()
        self._password = password
        self._unit_system = unit_system
        self._username = username
        self.is_connected: bool = False
        self.services = AudiService(self._auth, country, spin)
        self.vehicles: dict[str, Vehicle] = {}

    async def async_login(self) -> bool:
        """Login and retreive tokens."""
        if not self.is_connected:
            self.is_connected = await self._auth.async_connect(
                self._username, self._password, self._country
            )
        return self.is_connected

    async def async_update(self, vinlist: list[str] | None = None) -> bool:
        """Update data."""
        if not await self.async_login():
            return False

        # Update the state of all vehicles.
        try:
            if len(self._audi_vehicles) > 0:
                for vehicle in self._audi_vehicles:
                    await self.async_add_or_update_vehicle(vehicle, vinlist)

            else:
                vehicles_response = await self.services.async_get_vehicle_information()
                if vehicles_response.get("userVehicles") is None:
                    return False
                for response in vehicles_response.get("userVehicles"):
                    self._audi_vehicles.append(Vehicle(response, self.services))

                self.vehicles = {}
                for vehicle in self._audi_vehicles:
                    await self.async_add_or_update_vehicle(vehicle, vinlist)

            return True

        except IOError as exception:
            # Force a re-login in case of failure/exception
            self.is_connected = False
            _LOGGER.exception(exception)
            return False

    async def async_add_or_update_vehicle(
        self, vehicle: Vehicle, vinlist: list[str] | None
    ) -> None:
        """Add or Update vehicle."""
        if vehicle.vin is not None:
            if vinlist is None or vehicle.vin.lower() in vinlist:
                vupd = [
                    x for vin, x in self.vehicles.items() if vin == vehicle.vin.lower()
                ]
                if len(vupd) > 0:
                    if await vupd[0].async_fetch_data(self._connect_retries) is False:
                        self.is_connected = False
                else:
                    try:
                        if (
                            await vehicle.async_fetch_data(self._connect_retries)
                            is False
                        ):
                            self.is_connected = False
                        self.vehicles.update({vehicle.vin: vehicle})
                    except Exception:  # pylint: disable=broad-except
                        pass

    async def async_refresh_vehicle_data(self, vin: str) -> bool:
        """Refresh vehicle data."""
        if not await self.async_login():
            return False

        try:
            if vin not in self._excluded_refresh:
                _LOGGER.debug("Sending command to refresh data to vehicle %s", vin)
                await self.services.async_refresh_vehicle_data(vin)
                _LOGGER.debug("Successfully refreshed data of vehicle %s", vin)
                return True
        except ServiceNotFoundError as error:
            if error.args[0] in (403, 502):
                _LOGGER.debug("Refresh vehicle not supported")
                self._excluded_refresh.add(vin)
            elif error.args[0] == 401:
                _LOGGER.debug("Request unauthorized. Update and retry refresh")
                try:
                    self.is_connected = False
                    await self.async_login()
                    await self.services.async_refresh_vehicle_data(vin)
                except ServiceNotFoundError as err:
                    _LOGGER.error(
                        "Unable to refresh vehicle data of %s, despite trying again (%s)",
                        vin,
                        err,
                    )
            else:
                _LOGGER.error("Unable to refresh vehicle data of %s: %s", vin, error)
        except HttpRequestError as error:
            _LOGGER.error(
                "Unable to refresh vehicle data of %s: %s", vin, str(error).rstrip("\n")
            )
        return False

    async def async_refresh_vehicles(self) -> bool:
        """Refresh all vehicles data."""
        if not await self.async_login():
            return False

        for vin in self.vehicles:
            await self.async_refresh_vehicle_data(vin)

        return True

    async def async_switch_lock(self, vin: str, lock: bool) -> bool:
        """Set lock."""
        if not await self.async_login():
            return False

        try:
            action = "lock" if lock else "unlock"
            _LOGGER.debug("Sending command to %s to vehicle %s", action, vin)
            await self.services.async_lock(vin, lock)
            action = "locked" if lock else "unlocked"
            _LOGGER.debug("Successfully %s vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable to %s %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_switch_climater(self, vin: str, activate: bool) -> bool:
        """Set climatisation."""
        if not await self.async_login():
            return False

        try:
            action = "start" if activate else "stop"
            _LOGGER.debug(
                "Sending command to %s climatisation to vehicle %s", action, vin
            )
            await self.services.async_climater(vin, activate)
            action = "started" if activate else "stopped"
            _LOGGER.debug("Successfully %s climatisation of vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable to %s climatisation of vehicle %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_switch_charger(self, vin: str, activate: bool) -> bool:
        """Set charger."""
        if not await self.async_login():
            return False

        try:
            action: str = "start" if activate else "stop"
            _LOGGER.debug(
                "Sending command to %s charger to vehicle %s",
                action,
                vin,
            )
            await self.services.async_charger(vin, activate)
            action = "started" if activate else "stopped"
            _LOGGER.debug("Successfully %s charger of vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            action = "start" if activate else "stop"
            _LOGGER.error(
                "Unable to %s charger of vehicle %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_switch_window_heating(self, vin: str, activate: bool) -> bool:
        """Set window heating."""
        if not await self.async_login():
            return False

        try:
            action = "start" if activate else "stop"
            _LOGGER.debug(
                "Sending command to %s window heating to vehicle %s", action, vin
            )
            await self.services.async_window_heating(vin, activate)
            action = "started" if activate else "stopped"
            _LOGGER.debug("Successfully %s window heating of vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable to %s window heating of vehicle %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_switch_pre_heating(self, vin: str, activate: bool) -> bool:
        """Set pre heater."""
        if not await self.async_login():
            return False

        try:
            action = "start" if activate else "stop"
            _LOGGER.debug("Sending command to %s pre-heater to vehicle %s", action, vin)
            await self.services.async_pre_heating(vin, activate)
            action = "started" if activate else "stopped"
            _LOGGER.debug("Successfully %s pre-heater of vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable to %s pre-heater of vehicle %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_switch_ventilation(self, vin: str, activate: bool) -> bool:
        """Set charger."""
        if not await self.async_login():
            return False

        try:
            action: str = "start" if activate else "stop"
            _LOGGER.debug(
                "Sending command to %s ventilation to vehicle %s",
                action,
                vin,
            )
            await self.services.async_ventilation(vin, activate)
            action = "started" if activate else "stopped"
            _LOGGER.debug("Successfully %s charger of vehicle %s", action, vin)
            return True
        except ServiceNotFoundError as error:
            action = "start" if activate else "stop"
            _LOGGER.error(
                "Unable to %s ventilation of vehicle %s: %s",
                action,
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_set_honk_flash(
        self, vin: str, mode: Literal["honk", "flash"], duration: int
    ) -> bool:
        """Set honk/flash."""
        if not await self.async_login():
            return False

        try:
            _LOGGER.debug("Sending command Honk/Flash to vehicle %s", vin)
            await self.services.async_set_honkflash(vin, mode, duration)
            _LOGGER.debug("Successfully Honk/Flash of vehicle %s", vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable Honk/Flash of vehicle %s: %s",
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_set_charger_max_current(self, vin: str, current: int = 32) -> bool:
        """Set pre heater."""
        if not await self.async_login():
            return False

        try:
            _LOGGER.debug("Sending command max current to vehicle %s", vin)
            await self.services.async_set_charger_max(vin, current)
            _LOGGER.debug("Successfully set max current of vehicle %s", vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable set max current of vehicle %s: %s",
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_set_climater_temperature(
        self,
        vin: str,
        temperature: float,
        source: Literal["electric", "auxiliary", "automatic"],
    ) -> bool:
        """Set temperature of climater."""
        if not await self.async_login():
            return False

        try:
            _LOGGER.debug(
                "Sending command to climater (%s) [%s] to vehicle %s",
                temperature,
                source,
                vin,
            )
            await self.services.async_climater_temp(vin, temperature, source)
            _LOGGER.debug("Successfully settings climater of vehicle %s", vin)
            return True
        except ServiceNotFoundError as error:
            _LOGGER.error(
                "Unable to set climatisater of vehicle %s:  %s",
                vin,
                str(error).rstrip("\n"),
            )
            return False

    async def async_set_heater_source(
        self,
        source: Literal["electric", "auxiliary", "automatic"],
    ) -> None:
        """Set heater source."""
        self.services.set_heater_source(source)

    def set_control_duration(self, duration: int) -> None:
        """Set ventilation/preheating duration."""
        self.set_control_duration(duration)
