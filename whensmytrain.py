#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""

When's My Train?
(c) 2011-12 Chris Applegate (chris AT qwghlm DOT co DOT uk)
Released under the MIT License

A Twitter bot that takes requests for a Tube or DLR train, and replies with the real-time data from TfL on Twitter

Inherits many methods and data structures from WhensMyTransport, including: loading the databases, config, connecting to Twitter,
reading @ replies, replying to them, checking new followers, following them back

This module just does work specific to trains: Parsing & interpreting a train-specific message, and looking it up against the database of
stations and lines, checking the TfL Tube and DLR APIs and formatting an appropriate reply to be sent back
"""
from abc import ABCMeta
import argparse
import logging
from pprint import pprint

from whensmytransport import WhensMyTransport
from lib.dataparsers import parse_dlr_data, parse_tube_data
from lib.exceptions import WhensMyTransportException
from lib.models import RailStation, NullDeparture
from lib.stringutils import get_best_fuzzy_match
from lib.textparser import WMTTrainParser

# LINE_NAMES is of format:
#     (code, name): [list of alternative spellings]
LINE_NAMES = {
    ('B', 'Bakerloo'):           [],
    ('C', 'Central'):            [],
    ('O', 'Circle'):             [],
    ('D', 'District'):           [],
    ('H', 'Hammersmith & City'): ['Hammersmith and City', 'H&C'],
    ('J', 'Jubilee'):            ['Jubillee'],
    ('M', 'Metropolitan'):       ['Met'],
    ('N', 'Northern'):           [],
    ('P', 'Piccadilly'):         ['Picadilly'],
    ('V', 'Victoria'):           [],
    ('W', 'Waterloo & City'):    ['Waterloo and City', 'W&C'],
    ('DLR', 'DLR'):              ['Docklands Light Rail', 'Docklands Light Railway', 'Docklands'],
}


class WhensMyTrain(WhensMyTransport):
    """
    Class for the @WhensMyDLR and @WhensMyTube bots. This inherits from the WhensMyTransport and provides specialist functionality for when
    there is a limited number of stations and they have well-known, universally agreed names, which is normally railways and not buses.
    """
    __metaclass__ = ABCMeta

    def __init__(self, instance_name, testing=False):
        """
        Constructor
        """
        WhensMyTransport.__init__(self, instance_name, testing)
        self.allow_blank_tweets = True
        self.parser = WMTTrainParser()

        # Create lookup dict for line names
        self.line_lookup = dict([(name, name) for (_code, name) in LINE_NAMES.keys()])
        for ((_code, name), alternatives) in LINE_NAMES.items():
            self.line_lookup.update(dict([(alternative, name) for alternative in alternatives]))

    def process_individual_request(self, requested_line, origin, destination, direction, position):
        """
        Take an individual line, with either origin or position, and work out which station the user is
        referring to, and then get times for it. Filter trains by destination, or direction
        """
        # Try and work out line name and code if one has been requested
        line_code, line_name = None, None
        if requested_line:
            line_name = self.line_lookup.get(requested_line, None) or get_best_fuzzy_match(requested_line, self.line_lookup.values())
            if not line_name:
                raise WhensMyTransportException('nonexistent_line', requested_line)
            line_code = get_line_code(line_name)
            if line_name != 'DLR':
                line_name += " Line"

        # Try and work out what departure station has been requested
        if position:
            logging.debug("Attempting to get closest to user's position: %s on line code %s", position, line_code)
            station = self.get_station_by_geolocation(position, line_code)
            # There will always be a nearest station so no need to check for non-existence
        elif origin:
            logging.debug("Attempting to get a fuzzy match on origin %s on line code %s", origin, line_code)
            station = self.get_station_by_station_name(origin, line_code)
            if not station:
                raise WhensMyTransportException('rail_station_name_not_found', origin, line_name or "Tube")
        # XXX is the code for a station that does not have TrackerNet data on the API
        if station.code == "XXX":
            raise WhensMyTransportException('rail_station_not_in_system', station.name)

        # If user has specified a destination, work out what it is, and check a direct route to it exists
        destination_name = None
        if destination:
            logging.debug("Attempting to get a fuzzy match on destination %s on line code %s", origin, line_code)
            destination_name = self.get_canonical_station_name(destination, line_code) or None

        # Alternatively we may have had a direction given, so try that
        direction_name = None
        if not destination_name and direction:
            directions_lookup = {'n': 'Northbound', 'e': 'Eastbound', 'w': 'Westbound', 's': 'Southbound'}
            direction_name = directions_lookup.get(direction.lower()[0], None)
            if not direction_name:
                raise WhensMyTransportException('invalid_direction', direction)

        # We may not have been given a line - if so, try and work out what it might be from origin and destination
        if not line_code:
            lines = self.geodata.get_lines_serving(station.code, destination_name)
            # If no lines produced, then there must be no direct route between origin and destination. This will never happen
            # if there is no destination specified, as every origin has at least one line serving it
            if not lines:
                raise WhensMyTransportException('no_direct_route', station.name, destination_name, "Tube")
            # If more than one throw an exception due to ambiguity, then we have to ask the user for clarity
            if len(lines) > 1:
                if destination_name:
                    raise WhensMyTransportException('no_line_specified_to', station.name, destination_name)
                else:
                    raise WhensMyTransportException('no_line_specified', station.name)
            line_code = lines[0]
            line_name = get_line_name(line_code)

        # Some sanity-checking, to make sure our train is actually direct
        if destination_name and not self.geodata.direct_route_exists(station.name, destination_name, line_code):
            raise WhensMyTransportException('no_direct_route', station.name, destination_name, line_name)

        # All being well, we can now get the departure data for this station and return it
        departure_data = self.get_departure_data(station, line_code, must_stop_at=destination_name, direction=direction_name)
        if departure_data:
            return "%s to %s" % (station.get_abbreviated_name(), str(departure_data))
        else:
            if destination_name:
                raise WhensMyTransportException('no_trains_shown_to', line_name, station.name, destination_name)
            elif direction_name:
                raise WhensMyTransportException('no_trains_shown_in_direction', direction_name, line_name, station.name)
            else:
                raise WhensMyTransportException('no_trains_shown', line_name, station.name)

    def get_station_by_geolocation(self, position, line_code=None):
        """
        Take a line and a tuple specifying latitude & longitude, and works out closest station
        """
        params = {}
        if line_code:
            params['line'] = line_code
        return self.geodata.find_closest(position, params, RailStation)

    def get_station_by_station_name(self, station_name, line_code=None):
        """
        Take a line and a string specifying station name, and work out matching for that name
        """
        params = {}
        if line_code:
            params['line'] = line_code
        return self.geodata.find_fuzzy_match(station_name, params, RailStation)

    def get_canonical_station_name(self, station_name, line_code):
        """
        Return just the string matching for a line code and station name, or blank if none exists
        """
        station_obj = self.get_station_by_station_name(station_name, line_code)
        return station_obj and station_obj.name or ""

    def get_departure_data(self, station, line_code, must_stop_at=None, direction=None):
        """
        Take a station object and a line ID, and get departure data for that station
        Returns a dictionary; keys are slot names (platform for DLR, direction for Tube), values lists of Train objects
        """
        #pylint: disable=W0108
        # Check if the station is open and if so (it will throw an exception if not), summon the data
        self.check_station_is_open(station)

        # Circle line these days is coded H as it shares with the Hammersmith & City
        if line_code == 'O':
            line_code = 'H'
        # DLR and Tube have different APIs
        if line_code == 'DLR':
            dlr_data = self.browser.fetch_xml_tree(self.urls.DLR_URL % station.code)
            departures = parse_dlr_data(dlr_data, station)
            null_constructor = lambda platform: NullDeparture("from " + platform)
        else:
            tube_data = self.browser.fetch_xml_tree(self.urls.TUBE_URL % (line_code, station.code))
            departures = parse_tube_data(tube_data, station, line_code)
            null_constructor = lambda direction: NullDeparture(direction)

        # Turn parsed destination & via names into canonical versions for this train so we can do lookups & checks
        for slot in departures:
            for train in departures[slot]:
                if train.destination != "Unknown":
                    train.destination = self.get_canonical_station_name(train.destination, line_code)
                if train.via:
                    train.via = self.get_canonical_station_name(train.via, line_code)

        # Deal with any departures filed under "Unknown", slotting them into Eastbound/Westbound if their direction is not known
        # (By a stroke of luck, all the stations this applies to - North Acton, Edgware Road, Loughton, White City - are on an east/west line)
        if "Unknown" in departures:
            for train in departures["Unknown"]:
                destination_station = self.get_station_by_station_name(train.destination, line_code)
                if not destination_station:
                    continue
                if destination_station.location_easting < station.location_easting:
                    departures.add_to_slot("Westbound", train)
                else:
                    departures.add_to_slot("Eastbound", train)
            del departures["Unknown"]

        # For any non-empty list of departures, filter out any that terminate here. Note that existing empty lists remain empty and are not deleted
        does_not_terminate_here = lambda train: train.destination != station.name
        departures.filter(does_not_terminate_here, delete_existing_empty_slots=False)
        # If we've specified a station to stop at, filter out any that do not stop at that station or are not in its direction
        # Note that unlike the above, this will turn all existing empty lists into Nones (and thus deletable) as well
        if must_stop_at:
            filter_by_stop_at = lambda train: self.geodata.does_train_stop_at(station.name, must_stop_at, train)
            departures.filter(filter_by_stop_at, delete_existing_empty_slots=True)
        # Else filter by direction - Tubs is already classified by direction, DLR is not direction-aware so must calculate manually
        elif direction:
            if line_code == 'DLR':
                filter_by_direction = lambda train: self.geodata.is_correct_direction(station.name, train.destination, direction, line_code)
                departures.filter(filter_by_direction, delete_existing_empty_slots=True)
            else:
                for slot in list(departures):
                    if slot != direction:
                        del departures[slot]
        departures.cleanup(null_constructor)
        return departures

    def check_station_is_open(self, station):
        """
        Check to see if a station is open, return True if so, throw an exception if not
        """
        # If we get an exception with fetching this data, don't worry about it
        try:
            status_data = self.browser.fetch_xml_tree(self.urls.STATUS_URL)
        except WhensMyTransportException:
            return True
        # Find every station status, and if it matches our station and it is closed, throw an exception to alert the user
        for station_status in status_data.findall('StationStatus'):
            station_node = station_status.find('Station')
            status_node = station_status.find('Status')
            if station_node.attrib['Name'] == station.name and status_node.attrib['Description'] == 'Closed':
                raise WhensMyTransportException('tube_station_closed', station.name, station_status.attrib['StatusDetails'].strip().lower())
        return True


def get_line_code(line_name):
    """
    Return the TfL line code for the line requested
    """
    lookup = dict([(name, code) for (code, name) in LINE_NAMES.keys()])
    return lookup.get(line_name, None)


def get_line_name(line_code):
    """
    Return the full official Line name for the line code requested
    """
    lookup = dict([(code, name) for (code, name) in LINE_NAMES.keys()])
    return lookup.get(line_code, None)

# If this script is called directly, check our Tweets and Followers, and reply/follow as appropriate
# Instance name comes from command line, all other config is done in the file config.cfg
if __name__ == "__main__":
    #pylint: disable=C0103
    parser = argparse.ArgumentParser(description="Run When's My Tube? or When's My DLR?")
    parser.add_argument("instance_name", action="store", help="Name of the instance to run (e.g. whensmytube, whensmydlr)")
    instance = parser.parse_args().instance_name
    if instance in ("whensmytube", "whensmydlr"):
        try:
            WMT = WhensMyTrain(instance)
            WMT.check_tweets()
        except RuntimeError as err:
            print err
    else:
        print "Error - %s is not a valid instance name" % instance
