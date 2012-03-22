#!/usr/bin/env python
#pylint: disable=R0913
"""
Models and abstractions of concepts such as stations, trains, bus stops etc.
"""
from datetime import datetime, timedelta
import logging
import re

from lib.listutils import unique_values
from lib.stringutils import cleanup_name_from_undesirables, get_name_similarity


#
# Representations of stations, stops etc
#

class Location():
    """
    Class representing any kind of location (bus stop or station)
    """
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name

    def __str__(self):
        return self.name

    def __len__(self):
        return len(self.name)


class BusStop(Location):
    #pylint: disable=W0613
    """
    Class representing a bus stop
    """
    def __init__(self, name='', bus_stop_code='', heading=0, sequence=1, distance=0.0, run=0, **kwargs):
        Location.__init__(self, name)
        self.number = bus_stop_code
        self.heading = heading
        self.sequence = sequence
        self.distance_away = distance
        self.run = run

    def __cmp__(self, other):
        return cmp(self.distance_away, other.distance_away)

    def __len__(self):
        return len(self.get_normalised_name())

    def __hash__(self):
        return hash(str(self.run) + ',' + self.get_clean_name())

    def get_clean_name(self):
        """
        Get rid of TfL's ASCII symbols for Tube, National Rail, DLR & Tram from this stop's name
        """
        return cleanup_name_from_undesirables(self.name, ('<>', '#', r'\[DLR\]', '>T<'))

    def get_normalised_name(self):
        """
        Normalise a bus stop name, sorting out punctuation, capitalisation, abbreviations & symbols
        """
        # Upper-case and abbreviate road names
        normalised_name = self.get_clean_name().upper()
        for (word, abbreviation) in (('SQUARE', 'SQ'), ('AVENUE', 'AVE'), ('STREET', 'ST'), ('ROAD', 'RD'), ('STATION', 'STN'), ('PUBLIC HOUSE', 'PUB')):
            normalised_name = re.sub(r'\b' + word + r'\b', abbreviation, normalised_name)
        # Get rid of common words like 'The'
        for common_word in ('THE',):
            normalised_name = re.sub(r'\b' + common_word + r'\b', '', normalised_name)
        # Remove spaces and punctuation and return
        normalised_name = re.sub('[\W]', '', normalised_name)
        return normalised_name

    def get_similarity(self, test_string=''):
        """
        Custom similarity match for bus stops - takes into account many of them will be from train stations or bus stations
        """
        # Use the above function to normalise our names and facilitate easier comparison
        my_name = self.get_normalised_name()
        their_name = BusStop(test_string).get_normalised_name()
        # Exact match is obviously best
        if my_name == their_name:
            return 100

        # If user has specified a station or bus station, then a partial match at start or end of string works for us
        # We prioritise, just slightly, names that have the match at the beginning
        if re.search("(BUS)?STN", their_name):
            if my_name.startswith(their_name):
                return 95
            if my_name.endswith(their_name):
                return 94

        # If on the other hand, we add station or bus station to their name and it matches, that's also pretty good
        if re.search("^%s(BUS)?STN" % their_name, my_name):
            return 91
        if re.search("%s(BUS)?STN$" % their_name, my_name):
            return 90

        # Else fall back on name similarity
        return get_name_similarity(my_name, their_name)


class RailStation(Location):
    #pylint: disable=W0613
    """
    Class representing a railway station
    """
    def __init__(self, name='', code='', location_easting=0, location_northing=0, inner='', outer='', **kwargs):
        Location.__init__(self, name)
        self.code = code
        self.location_easting = location_easting
        self.location_northing = location_northing
        self.inner = inner
        self.outer = outer

    def get_abbreviated_name(self):
        """
        Take this station's name and abbreviate it to make it fit on Twitter better
        """
        # Stations we just have to cut down by hand
        translations = {
            "High Street Kensington": "High St Ken",
            "King's Cross St. Pancras": "Kings X St P",
            "Kensington (Olympia)": "Olympia",
            "W'wich Arsenal": "Woolwich A",
        }
        station_name = translations.get(self.name, self.name)

        # Punctuation marks can be cut down
        punctuation_to_remove = (r'\.', ', ', r'\(', r'\)', "'",)
        station_name = cleanup_name_from_undesirables(station_name, punctuation_to_remove)

        # Words like Road and Park can be slimmed down as well
        abbreviations = {
            'Bridge': 'Br',
            'Broadway': 'Bdwy',
            'Central': 'Ctrl',
            'Court': 'Ct',
            'Cross': 'X',
            'Crescent': 'Cresc',
            'East': 'E',
            'Gardens': 'Gdns',
            'Green': 'Grn',
            'Heathway': 'Hthwy',
            'Junction': 'Jct',
            'Market': 'Mkt',
            'North': 'N',
            'Park': 'Pk',
            'Road': 'Rd',
            'South': 'S',
            'Square': 'Sq',
            'Street': 'St',
            'Terminal': 'T',
            'Terminals': 'T',
            'West': 'W',
        }
        station_name = ' '.join([abbreviations.get(word, word) for word in station_name.split(' ')])

        # Any station with & in it gets only the initial of the second word - e.g. Elephant & C
        if station_name.find('&') > -1:
            station_name = station_name[:station_name.find('&') + 2]
        return station_name

    def get_similarity(self, test_string=''):
        """
        Custom similarity for train stations - takes into account fact many people use abbreviated names
        """
        # For low-scoring matches, we try matching between a string the same size as the user query, if its shorter than the name
        # being tested against, so this works for e.g. Kings Cross matching King's Cross St Pancras
        score = get_name_similarity(self.name, test_string)
        if len(test_string) < len(self.name):
            abbreviated_score = get_name_similarity(self.name[:len(test_string)], test_string)
            if abbreviated_score >= 85 and abbreviated_score > score:
                return min(abbreviated_score, 99)  # Never 100, in case it overrides an exact match
        return score

#
# Representations of departures
#


class Departure():
    """
    Class representing a train or bus
    """
    #pylint: disable=R0903
    def __init__(self, destination, departure_time):
        self.destination = destination
        self.departure_time = datetime.strptime(departure_time, "%H%M")
        # Deal with us being one side of midnight from the prescribed times
        if datetime.now().hour > self.departure_time.hour + 1:
            self.departure_time += timedelta(days=1)

    def __cmp__(self, other):
        return cmp(self.departure_time, other.departure_time)

    def __hash__(self):
        return hash('-'.join([self.get_destination(), self.get_departure_time()]))

    def __repr__(self):
        return "%s %s" % (self.get_destination(), self.get_departure_time())

    def __str__(self):
        return "%s %s" % (self.get_destination(), self.get_departure_time())

    def get_destination(self):
        """
        Returns destination (this usually get overridden)
        """
        return self.destination

    def get_departure_time(self):
        """
        Returns human-readable version of departure time, in the 24-hour clock
        """
        return self.departure_time.strftime("%H%M")


class NullDeparture(Departure):
    """
    Class representing a non-existent train or bus (i.e. when none is showing)
    """
    #pylint: disable=R0903
    def __init__(self, direction=""):
        Departure.__init__(self, "None", datetime.now().strftime("%H%M"))
        self.direction = direction

    def get_destination(self):
        """
        Returns destination (which in this case is an error message of sorts)
        """
        return "None shown going %s" % self.direction

    def get_departure_time(self):
        """
        Returns a blank departure time as there is no departure at all
        """
        return ""


class Bus(Departure):
    """
    Class representing a bus of any kind

    Unlike Trains, bus stop names for the same place can vary depending on which direction, so this takes this into account
    by recording the departure point as well
    """
    #pylint: disable=R0903
    def __init__(self, destination, departure_time, _departure_point=""):
        Departure.__init__(self, destination, departure_time)


class Train(Departure):
    """
    Class representing a train of any kind

    Unlike Buses, trains can have unknown destinations or complicated destination names
    """
    def __init__(self, destination, departure_time, direction=""):
        Departure.__init__(self, destination, departure_time)
        self.direction = direction

    def get_destination(self):
        """
        Return this train's destination in suitably shortened format
        """
        if self.destination == "Unknown":
            destination = "%s Train" % self.direction
        else:
            destination = RailStation(self.destination).get_abbreviated_name()
        return destination

    def get_clean_destination_name(self):
        """
        Get rid of "via" from a destination name to make
        it match easier to a canonical station name
        """
        return re.sub(" \(?via .*$", "", self.destination, flags=re.I)


class TubeTrain(Train):
    """
    Class representing a Tube train
    """
    #pylint: disable=W0231
    def __init__(self, destination, direction, departure_time, set_number, line_code, destination_code):
        manual_translations = {"Heathrow T123 + 5": "Heathrow Terminal 5"}
        destination = manual_translations.get(destination, destination)
        # Get rid of TfL's odd designations in the Destination field to make it compatible with our list of stations in the database
        # Destination names are full of garbage. What I would like is a database mapping codes to canonical names, but this does not exist
        destination = re.sub(r"\band\b", "&", destination, flags=re.I)

        # Destinations that are line names or Unknown get boiled down to Unknown
        if destination in ("Unknown", "Circle & Hammersmith & City") or destination.startswith("Circle Line") \
            or destination.endswith("Train") or destination.endswith("Line"):
            destination = "Unknown"
        else:
            # Regular expressions of instructions, depot names (presumably instructions for shunting after arrival), or platform numbers
            undesirables = ('\(rev to .*\)',
                            r'sidings?\b.*$',
                            '(then )?depot',
                            'ex (barnet|edgware) branch',
                            '\(ex .*\)',
                            '/ london road',
                            '27 Road',
                            '\(plat\. [0-9]+\)',
                            ' loop',
                            '\(circle\)')
            destination = cleanup_name_from_undesirables(destination, undesirables)
        Train.__init__(self, destination, departure_time, direction)
        self.set_number = set_number
        self.line_code = line_code
        self.destination_code = destination_code

    def __hash__(self):
        """
        Return hash value to enable ability to use as dictionary key
        """
        return hash(' '.join([self.set_number, self.destination_code, self.get_departure_time()]))

#
# Representation of a collection of Departures
#


class DepartureCollection:
    """
    Represents multiple Departures to different destinations, all going from the same rail station or a set of closely-related bus stops

    Acts like a dictionary. Items are lists of Departure objects, keys are "slots" that we have grouped these Departures into
            Buses are grouped by stop, and the keys are BusStop objects; items are lists of Bus objects
            Tube trains are grouped by direction, keys are "Eastbound", "Westbound" etc.; items are lists of TubeTrain objects
            DLR trains are grouped by platform, keys are "p1", "p2"; items are lists of Train objects

    Also handles filtering out unwanted departures (e.g. those terminating here, or not going where we want to), merging two slots are together
    and dealing with empty slots or slots we don't need
    """
    def __init__(self):
        self.departure_data = {}

    def __setitem__(self, slot, departures):
        self.departure_data[slot] = departures

    def __getitem__(self, slot):
        return self.departure_data[slot]

    def __delitem__(self, slot):
        del self.departure_data[slot]

    def __len__(self):
        return len(self.departure_data.keys())

    def __contains__(self, slot):
        return slot in self.departure_data

    def __str__(self):
        """
        Return a formatted string representing this data for use in a Tweet
        Departures are sorted by slot ID and then by earliest within that slot. Multiple times for same departure grouped together

        e.g. "Upminster 1200 1201 1204, Tower Hill 1203; Wimbledon 1200, Ealing Bdwy 1202 1204, Richmond 1208"
        """
        if not self.departure_data:
            return ""
        departures_output = {}
        # Output is a dictionary, each key a slot, each item a { destination:[list of times] } dictionary itself
        for slot in sorted(self.departure_data.keys()):
            departures_output[slot] = {}
            # Group by departure within each slot
            for departure in unique_values(sorted(self.departure_data[slot]))[:6]:
                destination = departure.get_destination()
                departures_output[slot][destination] = departures_output[slot].get(destination, []) + [departure.get_departure_time()]
            # Then sort grouped departures, earliest first within the slot. Different destinations separated by commas
            sort_earliest_departure_first = lambda pair1, pair2: cmp(pair1[1][0], pair2[1][0])
            departures_output[slot] = ["%s %s" % (destination, ' '.join(times)) for (destination, times) in sorted(departures_output[slot].items(), sort_earliest_departure_first)]

            departures_output[slot] = ', '.join([departure.strip() for departure in departures_output[slot]])
            # Bus stops get their names included as well, if there is a departure
            if isinstance(slot, BusStop) and not departures_output[slot].startswith("None shown"):
                departures_output[slot] = "%s to %s" % (slot.get_clean_name(), departures_output[slot])

        # Return slots separated by semi-colons
        return '; '.join([departures_output[slot] for slot in sorted(departures_output.keys())])

    def __repr__(self):
        return self.departure_data.__repr__()

    def add_to_slot(self, slot, departure):
        """
        Adds departure to slot, creating said slot if it doesn't already exist
        """
        self.departure_data[slot] = self.departure_data.get(slot, []) + [departure]

    def merge_common_slots(self):
        """
        Merges pairs of slots that serve the same destinations

        Some slots run departures the same way (e.g. at termini). The DLR doesn't tell us if this is the case, so we look at the destinations
        on each pair of slots and see if there is any overlap, using the set object and its intersection function. Any such
        overlapping slots, we merge their data together (though only for the first pair though, to be safe)
        """
        slot_pairs = [(slot1, slot2) for slot1 in self.departure_data.keys() for slot2 in self.departure_data.keys() if slot1 < slot2]
        common_slots = [(slot1, slot2) for (slot1, slot2) in slot_pairs
                             if set([t.destination for t in self.departure_data[slot1]]).intersection([t.destination for t in self.departure_data[slot2]])]
        for (slot1, slot2) in common_slots[:1]:
            logging.debug("Merging platforms %s and %s", slot1, slot2)
            self.departure_data[slot1 + ' & ' + slot2] = unique_values(self.departure_data[slot1] + self.departure_data[slot2])
            del self.departure_data[slot1], self.departure_data[slot2]

    def filter(self, filter_function, delete_existing_empty_slots=False):
        """
        Applies the function filter to each slot's list of Departures, and deletes any slots are emptied as a result

        If delete_existing_empty_slots is True, then this deletes pre-existing empty slots as well as ones that have been emptied by the filter function
        """
        for (slot, departures) in self.departure_data.items():
            if departures or delete_existing_empty_slots:
                departures = [d for d in departures if filter_function(d)]
                if not departures:
                    del self.departure_data[slot]
                else:
                    self.departure_data[slot] = departures

    def cleanup(self, null_object_constructor=NullDeparture):
        """
        Cleans up any empty slots within the data

        If no departures listed at all, then turn me into an empty dictionary
        Otherwise, any slot with an empty list as its value has it filled with a null object, which is constructed by null_object_constructor

        null_object_constructor is either a classname constructor, or a function that returns a created object
        e.g. lambda a: Constructor(a.lower())
        """
        # Make sure there is a departure in at least one slot
        if not [departures for departures in self.departure_data.values() if departures]:
            self.departure_data = {}
        # Go through list of slots and departures for them.  If there is a None, then there is no slot at all and we delete it
        # If there is an empty list (no departures) then we replace it with the null object specified ("None shown...").
        for slot in self.departure_data.keys():
            if self.departure_data[slot] == []:
                self.departure_data[slot] = [null_object_constructor(slot)]

