#!/usr/bin/env python
# -*- coding: utf-8 -*-
#pylint: disable=C0103,W0142,R0904,W0141,R0915,C0302
"""
A set of unit tests for When's My Bus?

IMPORTANT: These unit tests require Python 2.7, although When's My Bus will happily run in Python 2.6
"""
import sys
if sys.version_info < (2, 7):
    print "Sorry. While WhensMyBus can be run under Python 2.6, this unit testing script requires the more extensive unittest libraries in Python 2.7."
    print "Please upgrade!"
    sys.exit(1)

import argparse
import cProfile
import logging
import os.path
import pstats
import random
import re
import tempfile
import time
import unittest
from pprint import pprint

# Abort if a dependency is not installed
try:
    from whensmytransport import TESTING_TEST_LOCAL_DATA, TESTING_TEST_LIVE_DATA
    from whensmybus import WhensMyBus
    from whensmytrain import WhensMyTrain, LINE_NAMES, get_line_code, get_line_name

    from lib.dataparsers import parse_bus_data, parse_tube_data, parse_dlr_data
    from lib.exceptions import WhensMyTransportException
    from lib.geo import heading_to_direction, gridrefNumToLet, convertWGS84toOSEastingNorthing, LatLongToOSGrid, convertWGS84toOSGB36
    from lib.listutils import unique_values
    from lib.stringutils import capwords, get_name_similarity, get_best_fuzzy_match, cleanup_name_from_undesirables, gmt_to_localtime
    from lib.models import Location, RailStation, BusStop, Departure, NullDeparture, Train, TubeTrain, DLRTrain, Bus, DepartureCollection
    from lib.twitterclient import split_message_for_twitter
except ImportError as err:
    print """
Sorry, testing failed because a package that WhensMyTransport depends on is not installed. Reported error:

    %s

Missing packages can be downloaded as follows:

 * nltk: http://nltk.github.com/install.html
 * pygraph: http://code.google.com/p/python-graph/
 * tweepy: http://code.google.com/p/tweepy/
""" % err
    sys.exit(1)


HOME_DIR = os.path.dirname(os.path.abspath(__file__))
TEST_LEVEL = None


class FakeTweet:
    """
    Fake Tweet object to simulate tweepy's Tweet object being passed to various functions
    """
    #pylint: disable=R0903
    def __init__(self, text, coordinates=(), place=None, username='testuser'):
        self.user = lambda: 1
        self.user.screen_name = username
        self.text = text
        self.place = place
        self.geo = {}
        if coordinates:
            self.geo['coordinates'] = coordinates


class FakeDirectMessage:
    """
    Fake DirectMessage object to simulate tweepy's DirectMessage object being passed to various functions
    """
    #pylint: disable=R0903
    def __init__(self, text, username='testuser'):
        self.user = lambda: 1
        self.user.screen_name = username
        self.text = text


class WhensMyTransportTestCase(unittest.TestCase):
    """
    Parent Test case for all When's My * bots
    """
    def setUp(self):
        """
        Setup test
        """
        # setUp is left to the individual test suite
        self.bot = None

    def tearDown(self):
        """
        Tear down test
        """
        # Bit of a hack - we insist that any print statements are output, after the tests regardless of whether we failed or not
        self._resultForDoCleanups._mirrorOutput = True
        self.bot = None

    def _test_correct_exception_produced(self, tweet, exception_id, *string_params):
        """
        A generic test that the correct exception message is produced
        """
        # Get the message for the exception_id specified, applying C string formatting if applicable
        # Then escape it so that regular expression module doesn't accidentally interpret brackets etc
        expected_error = re.escape(WhensMyTransportException(exception_id, *string_params).value)

        # Try processing the relevant Tweet
        try:
            messages = self.bot.process_tweet(tweet)
        # If an exception is thrown, then see if its message matches the one we want
        except WhensMyTransportException as exc:
            self.assertRegexpMatches(exc.value, expected_error)
        # If there is no exception thrown, then see if the reply generated matches what we want
        else:
            for message in messages:
                self.assertRegexpMatches(message, expected_error)

    # Fundamental unit tests. These test libraries and essential methods and classes. These do not need a bot set up
    # and are thus independent of config and setup
    def test_exceptions(self):
        """
        Unit tests for WhensMyTransportException objects
        """
        exc = WhensMyTransportException()
        self.assertEqual(exc.value, str(exc))
        self.assertLessEqual(len(exc.get_user_message()), 115)

    def test_geo(self):
        """
        Unit test for geo conversion methods
        """
        # Test co-ordinate conversions on the location of St James's Park Station
        wgs84 = (51.4995893, -0.1342974)
        osgb36 = (51.4990781, -0.1326920)
        easting_northing = (529600, 179500)
        gridref = "TQ2960079500"

        self.assertEqual(convertWGS84toOSGB36(*wgs84)[:2], osgb36)
        self.assertEqual(LatLongToOSGrid(*osgb36), easting_northing)
        self.assertEqual(convertWGS84toOSEastingNorthing(*wgs84), easting_northing)
        self.assertEqual(gridrefNumToLet(*easting_northing), gridref)

        # Test heading_to_direction with a series of preset values
        for (heading, direction) in ((0, "North"), (90, "East"), (135, "SE"), (225, "SW"),):
            self.assertEqual(heading_to_direction(heading), direction)

    def test_listutils(self):
        """
        Unit test for listutils methods
        """
        test_list = [random.Random().randint(0, 10) for _i in range(0, 100)]
        unique_list = unique_values(test_list)
        # Make sure every value in new list was in old list
        for value in unique_list:
            self.assertTrue(value in test_list)
        # And that every value in the old list is now exactly once in new list
        for value in test_list:
            self.assertEqual(unique_list.count(value), 1)

    def test_stringutils(self):
        """
        Unit test for stringutils' methods
        """
        # Check capwords
        capitalised_strings = ("Bank", "Morden East", "King's Cross St. Pancras", "Kennington Oval via Charing X")
        for test_string in capitalised_strings:
            self.assertEqual(test_string, capwords(test_string))
            self.assertEqual(test_string, capwords(test_string.lower()))
            self.assertEqual(test_string, capwords(test_string.upper()))
            self.assertNotEqual(test_string.lower(), capwords(test_string))
            self.assertNotEqual(test_string.upper(), capwords(test_string))

        # Check to see cleanup string is working
        random_string = lambda a, b: "".join([chr(random.Random().randint(a, b)) for _i in range(0, 10)])
        dirty_strings = [random_string(48, 122) for _i in range(0, 10)]
        undesirables = ("a", "b+", "[0-9]", "^x")
        for dirty_string in dirty_strings:
            cleaned_string = cleanup_name_from_undesirables(dirty_string, undesirables)
            for undesirable in undesirables:
                self.assertIsNone(re.search(undesirable, cleaned_string, flags=re.I))

        # Check string similarities - 100 for identical strings, 90 or more for one character change
        # and nothing at all for a totally unidentical string
        similarity_string = random_string(65, 122)
        self.assertEqual(get_name_similarity(similarity_string, similarity_string), 100)
        self.assertGreaterEqual(get_name_similarity(similarity_string, similarity_string[:-1]), 90)
        self.assertEqual(get_name_similarity(similarity_string, random_string(48, 57)), 0)

        # Check to see most similar string gets picked out of an array of similar-looking strings, and that
        # with very dissimilar strings, there is no candidate at all
        similarity_candidates = (similarity_string[:3], similarity_string[:5], similarity_string[:9], "z" * 10)
        self.assertEqual(get_best_fuzzy_match(similarity_string, similarity_candidates), similarity_candidates[-2])
        dissimilarity_candidates = [random_string(48, 57) for _i in range(0, 10)]
        self.assertIsNone(get_best_fuzzy_match(similarity_string, dissimilarity_candidates))

        if time.localtime().tm_isdst:
            self.assertEqual(gmt_to_localtime("2359"), "0059")
        else:
            self.assertEqual(gmt_to_localtime("2359"), "2359")

    def test_tubeutils(self):
        """
        Unit test for helper functions in WhensMyTrain
        """
        self.assertEqual(get_line_code('Central'), 'C')
        self.assertEqual(get_line_code('Circle'), 'O')
        self.assertEqual(get_line_name('C'), 'Central')
        self.assertEqual(get_line_name('O'), 'Circle')
        for (line_code, line_name) in LINE_NAMES.keys():
            self.assertEqual(line_name, get_line_name(get_line_code(line_name)))
            self.assertEqual(line_code, get_line_code(get_line_name(line_code)))

    @unittest.skipIf(time.localtime()[3] < 2, "Arbitrary nature of test data fails at midnight")
    def test_models(self):
        """
        Unit tests for train, bus, station and bus stop objects
        """
        # Location fundamentals
        location_name = "Trafalgar Square"
        location = Location(location_name)
        self.assertEqual(str(location), location_name)
        self.assertEqual(repr(location), location_name)
        self.assertEqual(len(location), len(location_name))

        # BusStop fundamentals
        bus_stop = BusStop("TRAFALGAR SQUARE / CHARING CROSS STATION <> # [DLR] >T<", bus_stop_code='10000', distance=2.0, run=1)
        bus_stop2 = BusStop("CHARING CROSS STATION <> # [DLR} >T< / TRAFALGAR SQUARE", bus_stop_code='10001', distance=1.0, run=2)
        self.assertLess(bus_stop2, bus_stop)
        self.assertEqual(len(bus_stop), 26)
        self.assertEqual(hash(bus_stop), hash(BusStop("TRAFALGAR SQUARE / CHARING CROSS STATION <> # [DLR] >T<", run=1)))

        # BusStop complex functions
        for undesirable in ('<>', '#', r'\[DLR\]', '>T<'):
            self.assertNotRegexpMatches(bus_stop.get_clean_name(), undesirable)
        self.assertEqual(bus_stop.get_clean_name(), "Trafalgar Square / Charing Cross Station")
        self.assertEqual(bus_stop.get_normalised_name(), "TRAFALGARSQCHARINGCROSSSTN")
        self.assertEqual(bus_stop.get_similarity(bus_stop.name), 100)
        self.assertEqual(bus_stop.get_similarity("Charing Cross Station"), 94)
        self.assertEqual(bus_stop2.get_similarity("Charing Cross Station"), 95)
        self.assertEqual(bus_stop.get_similarity("Charing Cross"), 90)
        self.assertEqual(bus_stop2.get_similarity("Charing Cross"), 91)

        # RailStation complex functions
        station = RailStation("King's Cross St. Pancras", "KXX", 530237, 182944)
        station2 = RailStation("Earl's Court", "ECT")
        self.assertEqual(station.get_abbreviated_name(), "Kings X St P")
        self.assertEqual(station2.get_abbreviated_name(), "Earls Ct")
        self.assertEqual(station.get_similarity(station.name), 100)
        self.assertGreaterEqual(station.get_similarity("Kings Cross St Pancras"), 95)
        self.assertGreaterEqual(station.get_similarity("Kings Cross St Pancreas"), 90)
        self.assertGreaterEqual(station.get_similarity("Kings Cross"), 90)

        # Departure
        departure = Departure("Trafalgar Square", "2359")
        departure2 = Departure("Trafalgar Square", "0001")
        self.assertLess(departure, departure2)  # Fails if test run at 0000-0059
        self.assertEqual(hash(departure), hash(Departure("Trafalgar Square", "2359")))
        self.assertEqual(str(departure), "Trafalgar Square 2359")
        self.assertEqual(departure.get_destination(), "Trafalgar Square")
        self.assertEqual(departure.get_departure_time(), "2359")

        # NullDeparture
        null_departure = NullDeparture("East")
        self.assertEqual(null_departure.get_destination(), "None shown going East")
        self.assertEqual(null_departure.get_departure_time(), "")

        # Bus
        bus = Bus("Blackwall", "2359")
        bus2 = Bus("Blackwall", "0001")
        self.assertLess(bus, bus2)  # Fails if test run at 0000-0059
        self.assertEqual(bus.get_destination(), "Blackwall")

        # Train
        train = Train("Charing Cross via Bank", "2359")
        train2 = Train("Charing Cross via Bank", "0001")
        self.assertLess(train, train2)  # Fails if test run at 0000-0059
        self.assertEqual(train.get_destination(), "Charing X via Bank")

        # TubeTrain
        tube_train = TubeTrain("Charing Cross via Bank", "Northbound", "2359", "N", "001")
        tube_train2 = TubeTrain("Charing Cross via Bank then depot", "Northbound", "2359", "N", "001")
        tube_train3 = TubeTrain("Northern Line", "Northbound", "2359", "N", "001")
        tube_train4 = TubeTrain("Heathrow T123 + 5", "Westbound", "2359", "P", "001")
        self.assertEqual(hash(tube_train), hash(tube_train2))
        self.assertEqual(tube_train.get_destination(), "Charing X via Bank")
        self.assertEqual(tube_train3.get_destination(), "Northbound Train")
        self.assertEqual(tube_train4.get_destination(), "Heathrow T 5")
        self.assertEqual(tube_train.get_destination_no_via(), "Charing X")
        self.assertEqual(tube_train.get_via(), "Bank")

        # DLRTrain
        dlr_train = DLRTrain("Beckton", "1200")
        self.assertEqual(dlr_train.line_code, "DLR")

        # DepartureCollection fundamentals
        departures = DepartureCollection()
        departures[bus_stop] = [bus]
        self.assertEqual(departures[bus_stop], [bus])
        self.assertEqual(len(departures), 1)
        del departures[bus_stop]
        self.assertFalse(bus_stop in departures)

        # DepartureCollection for trains
        departures.add_to_slot(bus_stop, bus)
        departures.add_to_slot(bus_stop, bus2)
        self.assertEqual(str(departures), "Trafalgar Square / Charing Cross Station to Blackwall 2359 0001")
        departures[bus_stop2] = []
        departures.cleanup(lambda stop: NullDeparture("West"))
        self.assertEqual(str(departures), "None shown going West; Trafalgar Square / Charing Cross Station to Blackwall 2359 0001")
        departures.add_to_slot(bus_stop, Bus("Leamouth", "2358"))
        self.assertEqual(str(departures), "None shown going West; Trafalgar Square / Charing Cross Station to Leamouth 2358, Blackwall 2359 0001")

        # DepartureCollection for trains
        departures = DepartureCollection()
        departures["P1"] = [Train("Bank", "1210"), Train("Tower Gateway", "1203"), Train("Bank", "1200")]
        departures["P2"] = [Train("Tower Gateway", "1205"), Train("Tower Gateway", "1212"), Train("Bank", "1207")]
        departures["P3"] = [Train("Lewisham", "1200"), Train("Lewisham", "1204"), Train("Lewisham", "1208")]
        departures["P4"] = []
        departures.merge_common_slots()
        departures.cleanup(lambda platform: NullDeparture("from %s" % platform))
        self.assertEqual(str(departures), "Bank 1200 1207 1210, Tower Gateway 1203 1205; Lewisham 1200 1204 1208; None shown going from P4")
        departures.filter(lambda train: train.get_destination() != "Lewisham")
        self.assertEqual(str(departures), "Bank 1200 1207 1210, Tower Gateway 1203 1205; None shown going from P4")
        departures["P4"] = []
        departures.filter(lambda train: train.get_destination() != "Tower Gateway", True)
        self.assertEqual(str(departures), "Bank 1200 1207 1210")

    # Fundamental non-unit functionality tests. These need a WMT bot set up and are thus contingent on a
    # config.cfg files to test things such as a particular instance's databases, geocoder and browser
    def test_init(self):
        """
        Test to see if we can load the class in the first place
        """
        self.assertIsNotNone(self.bot)

    def test_browser(self):
        """
        Unit tests for WMTBrowser object
        """
        for filename in ("test.xml", "test.json"):
            url = "file://" + HOME_DIR + "/testdata/unit/" + filename
            if filename.endswith('json'):
                data = self.bot.browser.fetch_json(url)
                self.assertEqual(data['answer_to_life_universe_everything'], 42)
            elif filename.endswith('xml'):
                data = self.bot.browser.fetch_xml_tree(url)
                self.assertEqual(data.find("answer[@key='to_life_universe_everything']").attrib['value'], '42')
            self.assertIn(url, self.bot.browser.cache)

        for filename in ("test_broken.json", "test_broken.xml"):
            url = "file://" + HOME_DIR + "/testdata/" + filename
            try:
                if filename.endswith('json'):
                    data = self.bot.browser.fetch_json(url)
                elif filename.endswith('xml'):
                    data = self.bot.browser.fetch_xml_tree(url)
            except WhensMyTransportException:
                continue
            finally:
                self.assertNotIn(url, self.bot.browser.cache)

    def test_database(self):
        """
        Unit tests for WMTDatabase object and to see if requisite database tables exist
        """
        self.bot.geodata.database.write_query("CREATE TEMPORARY TABLE test_data (key, value INT)")

        for key_value in (('a', 1), ('b', 2), ('b', 3)):
            self.bot.geodata.database.write_query("INSERT INTO test_data VALUES (?, ?)", key_value)
        row = self.bot.geodata.database.get_row("SELECT * FROM test_data WHERE key = 'a'")
        self.assertIsNotNone(row)
        self.assertEqual(row[1], 1)
        rows = self.bot.geodata.database.get_rows("SELECT * FROM test_data WHERE key = 'b' ORDER BY value")
        self.assertIsNotNone(rows)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][1], 3)
        value = self.bot.geodata.database.get_value("SELECT value FROM test_data WHERE key = 'a'")
        self.assertIsNotNone(value)
        self.assertEqual(value, 1)
        value = self.bot.geodata.database.get_value("SELECT value FROM test_data WHERE key = 'c'")
        self.assertIsNone(value)
        self.assertTrue(self.bot.geodata.database.check_existence_of('test_data', 'key', 'a'))
        self.assertEqual(self.bot.geodata.database.get_max_value('test_data', 'value', {}), 3)
        self.assertEqual(self.bot.geodata.database.make_where_statement('test_data', {}), (" 1 ", ()))
        self.assertEqual(self.bot.geodata.database.make_where_statement('test_data', {'key': 'a', 'value': 1}), ('"key" = ? AND "value" = ?', ('a', 1)))
        self.assertRaises(KeyError, self.bot.geodata.database.make_where_statement, 'test_data', {'foo': 'a'})

        self.bot.geodata.database.write_query("DROP TABLE test_data")

        for name in self.geodata_table_names:
            row = self.bot.geodata.database.get_row("SELECT name FROM sqlite_master WHERE type='table' AND name='%s'" % name)
            self.assertIsNotNone(row, '%s table does not exist' % name)

    @unittest.skipIf('--live-data' in sys.argv, "Data parser unit test will fail on live data")
    def test_dataparsers(self):
        """
        Unit tests for Data parsers objects
        """
        # Check against our test data and make sure we are correctly parsing & fetching the right objects from the data
        bus_data = parse_bus_data(self.bot.browser.fetch_json(self.bot.urls.BUS_URL % "53410"), '15')
        self.assertEqual(bus_data[0], Bus("Regent Street", gmt_to_localtime("1831")))
        tube_data = parse_tube_data(self.bot.browser.fetch_xml_tree(self.bot.urls.TUBE_URL % ("D", "ECT")), RailStation("Earl's Court"), "D")
        self.assertEqual(tube_data["Eastbound"][0], TubeTrain("Edgware Road", "Eastbound", "2139", "D", "075"))
        dlr_data = parse_dlr_data(self.bot.browser.fetch_xml_tree(self.bot.urls.DLR_URL % "pop"), RailStation("Poplar"))
        self.assertEqual(dlr_data['P1'][0], Train("Beckton", "2107"))

    def test_geocoder(self):
        """
        Unit tests for Geocoder objects
        """
        if not self.bot.geocoder:
            return
        test_locations = {"Blackfriars station": (51.5116, -0.1036),
                          "Buckingham Palace": (51.5012, -0.1425),
                          "Wembley Stadium": (51.5558, -0.2797),
                          "qwerty": None}
        for (name, value) in test_locations.items():
            geocode_url = self.bot.geocoder.get_geocode_url(name)
            geodata = self.bot.browser.fetch_json(geocode_url)
            points = self.bot.geocoder.parse_geodata(geodata)
            if value is None:
                self.assertFalse(points)
            else:
                self.assertAlmostEqual(points[0][0], value[0], places=3)
                self.assertAlmostEqual(points[0][1], value[1], places=3)

    def test_logger(self):
        """
        Unit tests for system logging
        """
        self.assertGreater(len(logging.getLogger('').handlers), 0)

    def test_settings(self):
        """
        Test to see if settings database does not exist
        """
        query = "SELECT name FROM sqlite_master WHERE type='table' AND name='%s_settings'" % self.bot.instance_name
        row = self.bot.twitter_client.settings.settingsdb.get_row(query)
        self.assertIsNotNone(row, 'Settings table does not exist')
        test_time = int(time.time())
        self.bot.twitter_client.settings.update_setting("_test_time", test_time)
        self.assertEqual(test_time, self.bot.twitter_client.settings.get_setting("_test_time"))

    def test_twitter_tools(self):
        """
        Test to see if Twitter helper functions such as message splitting work properly
        """
        (message1, message2) = ("486 Charlton Stn / Charlton Church Lane to Bexleyheath Ctr 1935",
                                "Charlton Stn / Charlton Church Lane to North Greenwich 1934")
        message = "%s; %s" % (message1, message2)
        split_messages = [u"%s…" % message1, u"…%s" % message2]
        self.assertEqual(split_message_for_twitter(message, '@test_username'), split_messages)

    def test_twitter_client(self):
        """
        Test to see if our OAuth login details are correct
        """
        self.assertTrue(self.bot.twitter_client.api.verify_credentials(), msg="Twitter API credentials failed. Fix or try running with --local-only")

    #
    # Tests that Tweets are in the right format
    #

    def test_politeness(self):
        """
        Test to see if we are replying to polite messages correctly
        """
        tweet = FakeTweet(self.at_reply + 'Thank you!')
        self.assertFalse(self.bot.process_tweet(tweet))
        self.assertRegexpMatches(self.bot.check_politeness(tweet)[0], 'No problem')

    def test_mention(self):
        """
        Test to confirm we are ignoring Tweets that are just mentions and not replies
        """
        tweet = FakeTweet('Hello @%s' % self.bot.username)
        self.assertFalse(self.bot.validate_tweet(tweet))

    def test_talking_to_myself(self):
        """
        Test to confirm we are ignoring Tweets from the bot itself
        """
        tweet = FakeTweet(self.at_reply + self.standard_test_data[0][0], username=self.bot.username)
        self.assertFalse(self.bot.validate_tweet(tweet))

    def test_blank_tweet(self):
        """
        Test to confirm we are ignoring blank replies
        """
        for message in ('',
                     ' ',
                     '         '):
            tweet = FakeTweet(self.at_reply + message)
            self._test_correct_exception_produced(tweet, 'blank_%s_tweet' % self.bot.instance_name.replace('whensmy', ''))
            direct_message = FakeDirectMessage(message)
            self._test_correct_exception_produced(direct_message, 'blank_%s_tweet' % self.bot.instance_name.replace('whensmy', ''))

    #
    # Geotagging tests
    #

    def test_no_geotag(self):
        """
        Test to confirm lack of geotag handled OK
        """
        for test_data in self.standard_test_data:
            request = "%s" % test_data[0]
            tweet = FakeTweet(self.at_reply + request)
            self._test_correct_exception_produced(tweet, 'no_geotag', request)
            direct_message = FakeDirectMessage(request)
            self._test_correct_exception_produced(direct_message, 'dms_not_taggable', request)

    def test_placeinfo_only(self):
        """
        Test to confirm ambiguous place information handled OK
        """
        for test_data in self.standard_test_data:
            request = "%s" % test_data[0]
            tweet = FakeTweet(self.at_reply + request, place='foo')
            self._test_correct_exception_produced(tweet, 'placeinfo_only', request)

    def test_not_in_uk(self):
        """
        Test to confirm geolocations outside UK handled OK
        """
        for test_data in self.standard_test_data:
            request = "%s" % test_data[0]
            tweet = FakeTweet(self.at_reply + request, (40.748433, -73.985656))  # Empire State Building, New York
            self._test_correct_exception_produced(tweet, 'not_in_uk')

    def test_not_in_london(self):
        """
        Test to confirm geolocations outside London handled OK
        """
        for test_data in self.standard_test_data:
            request = "%s" % test_data[0]
            tweet = FakeTweet(self.at_reply + request, (55.948611, -3.200833))  # Edinburgh Castle, Edinburgh
            self._test_correct_exception_produced(tweet, 'not_in_london')

    @unittest.skipIf('--live-data' in sys.argv, "Expected responses to messages not replicable with live data")
    def test_nonstandard_messages(self):
        """
        Test to confirm a message that can be troublesome comes out OK
        """
        for (request, mandatory_items, forbidden_items) in self.nonstandard_test_data:
            message = self.at_reply + request
            print message
            tweet = FakeTweet(message)
            t1 = time.time()
            results = self.bot.process_tweet(tweet)
            t2 = time.time()
            self.assertTrue(results)
            for result in results:
                print result
                # Result exists, no TfL garbag  e please, and no all-caps either
                self.assertTrue(result)
                for unwanted in ('<>', '#', '\[DLR\]', '>T<'):
                    self.assertNotRegexpMatches(result, unwanted)
                self.assertNotEqual(result, result.upper())
                for mandatory_item in mandatory_items:
                    self.assertRegexpMatches(result, mandatory_item)
                for forbidden_item in forbidden_items:
                    self.assertNotRegexpMatches(result, forbidden_item)
            print 'Processing of Tweet took %0.3f ms\r\n' % ((t2 - t1) * 1000.0,)


class WhensMyBusTestCase(WhensMyTransportTestCase):
    """
    Main Test Case for When's My Bus
    """
    #pylint: disable=R0904
    def setUp(self):
        """
        Setup test
        """
        try:
            self.bot = WhensMyBus(testing=TEST_LEVEL)
        except RuntimeError as exc:
            print exc
            self.tearDown()
            sys.exit(1)

        self.at_reply = '@%s ' % self.bot.username
        self.geodata_table_names = ('locations', )

        # Route Number, Origin Name, Origin Number, Origin Longitude, Origin Latitude, Dest Name, Dest Number, Expected Origin, Unwanted Destination
        self.standard_test_data = (
            ('15',         'Limehouse Station', '53452', 51.5124, -0.0397, 'Poplar',           '73923', 'Limehouse Station', 'Regent Street'),
            ('425 25 205', 'Bow Road Station',  '55489', 51.5272, -0.0247, 'Mile End station', '76239', 'Bow Road Station',  '(Bow Church|Ilford|Stratford)'),
        )
        # Troublesome destinations & data
        self.nonstandard_test_data = (
            # Hoxton is mistaken as Brixton
            ('243 from Hoxton',
                ('Hoxton Station / Geffrye Museum',),
                ('Brixton',)),
            # Postcodes should be doable with a geocoder
            ('55 from EC1M 4PN',
                ('St John Street',),
                ()),
            # 103 has more than 2 runs, check we delete the empty one
            ('103 from Romford Station',
                ('Romford Station',),
                ('None shown',)),
            # Dodgy
            ('the 15 from st pauls churchyard',
                ("St Paul's Churchyard",),
                ('None shown',)),
        )

    def _test_correct_successes(self, tweet, routes_specified, expected_origin, destination_to_avoid=''):
        """
        Generic test to confirm a Bus Tweet is being processed correctly
        """
        print tweet.text
        t1 = time.time()
        results = self.bot.process_tweet(tweet)
        self.assertTrue(results)
        t2 = time.time()
        self.assertTrue(results)
        for result in results:
            print result
            # Result exists, no TfL garbage please, and no all-caps either
            self.assertTrue(result)
            for unwanted in ('<>', '#', '\[DLR\]', '>T<'):
                self.assertNotRegexpMatches(result, unwanted)
            self.assertNotEqual(result, result.upper())
            # Should say one of our route numbers, expected origin and a time
            route_regex = "^(%s)" % '|'.join(routes_specified.upper().replace(',', '').split(' '))
            self.assertRegexpMatches(result, route_regex)
            if result.find("None shown") > -1:
                self.assertRegexpMatches(result, 'None shown going (North|NE|East|SE|South|SW|West|NW)(;|$)')
            else:
                self.assertRegexpMatches(result, '(%s to .* [0-9]{4})' % expected_origin)
            # If we have specified a direction or destination, we should not be seeing buses going the other way
            # and the expected origin should therefore only be repeated once
            if destination_to_avoid:
                self.assertNotRegexpMatches(result, destination_to_avoid)
                self.assertNotRegexpMatches(result, ";")
                self.assertEqual(result.count(expected_origin), 1)
            else:
                self.assertRegexpMatches(result, ";")

        print 'Processing of Tweet took %0.3f ms\r\n' % ((t2 - t1) * 1000.0,)

    #
    # Core functionality tests
    #

    def test_location(self):
        """
        Unit tests for WMTLocation object and the bus database
        """
        self.assertEqual(self.bot.geodata.find_closest((51.5124, -0.0397), {'run': '1', 'route': '15'}, BusStop).number, "53410")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Limehouse Sta", {'run': '1', 'route': '15'}, BusStop).number, "53410")
        self.assertEqual(self.bot.geodata.find_exact_match({'run': '1', 'route': '15', 'name': 'LIMEHOUSE TOWN HALL'}, BusStop).number, "48264")
        self.assertTrue(self.bot.geodata.database.check_existence_of('locations', 'bus_stop_code', '47001'))
        self.assertFalse(self.bot.geodata.database.check_existence_of('locations', 'bus_stop_code', '47000'))
        self.assertEqual(self.bot.geodata.database.get_max_value('locations', 'run', {}), 4)

    def test_no_bus_number(self):
        """
        Test to confirm we are ignoring Tweets that do not have bus numbers in them
        """
        message = 'Thanks!'
        tweet = FakeTweet(self.at_reply + message)
        self.assertFalse(self.bot.process_tweet(tweet))
        direct_message = FakeDirectMessage(message)
        self.assertFalse(self.bot.process_tweet(direct_message))

    def test_nonexistent_bus(self):
        """
        Test to confirm non-existent buses handled OK
        """
        for message in ('218 from Trafalgar Square', '   218 from Trafalgar Square', '   218   from Trafalgar Square #hashtag'):
            tweet = FakeTweet(self.at_reply + message)
            self._test_correct_exception_produced(tweet, 'nonexistent_bus', '218')
            direct_message = FakeDirectMessage(message)
            self._test_correct_exception_produced(direct_message, 'nonexistent_bus', '218')

    def test_textparser(self):
        """
        Tests for the natural language parser
        """
        (route, origin, destination) = ('A1', 'Heathrow Airport', '47000')
        routes = [route]
        self.assertEqual(self.bot.parser.parse_message(""),                                                 (None, None, None, None))
        self.assertEqual(self.bot.parser.parse_message("from %s to %s %s" % (origin, destination, route)),  (None, None, None, None))
        self.assertEqual(self.bot.parser.parse_message("%s" % route),                                       (routes, None, None, None))
        self.assertEqual(self.bot.parser.parse_message("%s %s" % (route, origin)),                          (routes, origin, None, None))
        self.assertEqual(self.bot.parser.parse_message("%s %s to %s" % (route, origin, destination)),       (routes, origin, destination, None))
        self.assertEqual(self.bot.parser.parse_message("%s from %s" % (route, origin)),                     (routes, origin, None, None))
        self.assertEqual(self.bot.parser.parse_message("%s from %s to %s" % (route, origin, destination)),  (routes, origin, destination, None))
        self.assertEqual(self.bot.parser.parse_message("%s to %s" % (route, destination)),                  (routes, None, destination, None))
        self.assertEqual(self.bot.parser.parse_message("%s to %s from %s" % (route, destination, origin)),  (routes, origin, destination, None))

    #
    # Request-based tests
    #

    def test_bad_stop_id(self):
        """
        Test to confirm bad stop IDs handled OK
        """
        message = '15 from 00000'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'bad_stop_id', '00000')  # Stop IDs begin at 47000
        direct_message = FakeDirectMessage(message)
        self._test_correct_exception_produced(direct_message, 'bad_stop_id', '00000')  # Stop IDs begin at 47000

    def test_stop_id_mismatch(self):
        """
        Test to confirm when route and stop do not match up is handled OK
        """
        message = '15 from 52240'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'stop_id_not_found', '15', '52240')  # The 15 does not go from Canary Wharf
        direct_message = FakeDirectMessage(message)
        self._test_correct_exception_produced(direct_message, 'stop_id_not_found', '15', '52240')

    def test_stop_name_nonsense(self):
        """
        Test to confirm when route and stop do not match up is handled OK
        """
        message = '15 from Eucgekewf78'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'stop_name_not_found', '15', 'Eucgekewf78')

    def test_standard_messages(self):
        """
        Generic test for standard-issue messages
        """
        #pylint: disable=W0612
        for (route, origin_name, origin_id, lat, lon, destination_name, destination_id, expected_origin, destination_to_avoid) in self.standard_test_data:

            # C-string format helper
            test_variables = dict([(name, eval(name)) for name in ('route', 'origin_name', 'origin_id', 'destination_name', 'destination_id')])

            # 5 types of origin (geotag, ID, name, ID without 'from', name without 'from') and 3 types of destination (none, ID, name)
            from_fragments = [value % test_variables for value in ("", " from %(origin_name)s",    " from %(origin_id)s", " %(origin_name)s", " %(origin_id)s")]
            to_fragments = [value % test_variables for value in ("", " to %(destination_name)s", " to %(destination_id)s")]

            for from_fragment in from_fragments:
                for to_fragment in to_fragments:
                    message = (self.at_reply + route + from_fragment + to_fragment)
                    if not from_fragment:
                        tweet = FakeTweet(message, (lat, lon))
                    else:
                        tweet = FakeTweet(message)
                    if (from_fragment.find(origin_id) > -1) or to_fragment:
                        self._test_correct_successes(tweet, route, expected_origin, destination_to_avoid)
                    else:
                        self._test_correct_successes(tweet, route, expected_origin)

    def test_multiple_routes(self):
        """
        Test multiple routes from different bus stops in the same area (unlike the above function which
        tests multiple routes going in the same direction, from the same stop(s)
        """
        test_data = (("277 15", (51.511694, -0.030286), "(East India Dock Road|Limehouse Town Hall)"),)

        for (route, position, expected_origin) in test_data:
            tweet = FakeTweet(self.at_reply + route, position)
            self._test_correct_successes(tweet, route, expected_origin)


class WhensMyTubeTestCase(WhensMyTransportTestCase):
    """
    Main Test Case for When's My Tube
    """
    def setUp(self):
        """
        Setup test
        """
        try:
            self.bot = WhensMyTrain("whensmytube", testing=TEST_LEVEL)
        except RuntimeError as exc:
            print exc
            self.tearDown()
            sys.exit(1)

        self.at_reply = '@%s ' % self.bot.username
        self.geodata_table_names = ('locations', )

        # Regular test data
        #
        # Line, requested stop, latitude, longitude, destination, direction, correct stop name, unwanted destination (if destination or direction specified)
        self.standard_test_data = (
           ('District Line',        "Earl's Court",  51.4913, -0.1947, "Edgware Road", "Eastbound",   "Earls Ct",      'Wimbledon'),
           ('Victoria Line',        "Victoria",      51.4966, -0.1448, "Walthamstow",  "Northbound",  "Victoria",      'Brixton'),
           ('Waterloo & City Line', "Waterloo",      51.5031, -0.1132, "Bank",         "Eastbound",   "Waterloo",      'Moorgate'),
           ('DLR',                  'Poplar',        51.5077, -0.0174, 'All Saints',   "Northbound",  'Poplar',        'Lewisham'),
        )
        self.nonstandard_test_data = (
            # Hainault Loop and Northern Line handled correctly
            ("Central Line from White City to Redbridge",
                ("Hainault via Newbury Pk", "Woodford via Hainault"),
                ("Epping [0-9]{4}",)),
            ("Northern Line from Camden Town to Kennington",
                ("via Bank", "via Charing X"),
                ("High Barnet [0-9]{4}",)),
            # Directional sussing at a tricky station
            ("Circle Line from Edgware Road to Moorgate",
                ("Eastbound Train",),
                ("Hammersmith [0-9]{4}",)),
            # Handle ably when no line is specified but only one line serves the origin
            ('Arsenal',
                ('Cockfosters', 'Heathrow'),
                (str(WhensMyTransportException('no_line_specified', 'Arsenal')),)),
            # Handle ably when no line is specified but only one line serves both origin and destination
            ("Earl's Court to Plaistow",
                ('Upminster',),
                (str(WhensMyTransportException('no_line_specified_to', "Earl's Court", "Plaistow")),)),
            # Handle ably when no line is specified, there exists more than one line to get there, but we pick fastest
            ("Stockwell to Euston",
                ('Walthamstow Ctrl',),
                (str(WhensMyTransportException('no_line_specified_to', "Stockwell", "Euston")),)),            
        )

    def _test_correct_successes(self, tweet, _routes_specified, expected_origin, destination_to_avoid=''):
        """
        Generic test to confirm Tube Tweet is being processed correctly
        """
        print tweet.text
        t1 = time.time()
        results = self.bot.process_tweet(tweet)
        self.assertTrue(results)
        t2 = time.time()
        self.assertTrue(results)
        for result in results:
            print result
            self.assertTrue(result)
            self.assertNotEqual(result, result.upper())
            self.assertRegexpMatches(result, r"%s to .* [0-9]{4}" % expected_origin)
            if destination_to_avoid:
                self.assertNotRegexpMatches(result, destination_to_avoid)
        print 'Processing of Tweet took %0.3f ms\r\n' % ((t2 - t1) * 1000.0,)

    #
    # Core functionality tests
    #

    def test_location(self):
        """
        Unit tests for WMTLocation object and the Tube database
        """
        # Test station-finding works
        self.assertEqual(self.bot.geodata.find_closest((51.529444, -0.126944), {}, RailStation).code, "KXX")
        self.assertEqual(self.bot.geodata.find_closest((51.529444, -0.126944), {'line': 'M'}, RailStation).code, "KXX")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Kings Cross", {}, RailStation).code, "KXX")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Kings Cross", {'line': 'M'}, RailStation).code, "KXX")

        # Test route-tracing works as expected
        self.assertIn(('Oxford Circus', '', 'Victoria'), self.bot.geodata.describe_route("Stockwell", "Euston"))
        self.assertIn(('Charing Cross', '', 'Northern'), self.bot.geodata.describe_route("Stockwell", "Euston", "N"))
        self.assertIn(('Bank', '', 'Northern'), self.bot.geodata.describe_route("Stockwell", "Euston", "N", "Bank"))

        # Test route-testing works as expected
        west_ruislip = self.bot.geodata.find_fuzzy_match("West Ruislip", {}, RailStation)
        hainault = self.bot.geodata.find_fuzzy_match("Hainault", {}, RailStation)
        roding_valley = self.bot.geodata.find_fuzzy_match("Roding Valley", {}, RailStation)
        wanstead = self.bot.geodata.find_fuzzy_match("Wanstead", {}, RailStation)
        snaresbrook = self.bot.geodata.find_fuzzy_match("Snaresbrook", {}, RailStation)
        heathrow123 = self.bot.geodata.find_fuzzy_match("Heathrow Terminals 1, 2, 3", {}, RailStation)
        heathrow4 = self.bot.geodata.find_fuzzy_match("Heathrow Terminal 4", {}, RailStation)
        self.assertTrue(self.bot.geodata.direct_route_exists(west_ruislip, west_ruislip, "C"))
        self.assertTrue(self.bot.geodata.direct_route_exists(west_ruislip, hainault, "C"))
        self.assertTrue(self.bot.geodata.direct_route_exists(west_ruislip, roding_valley, "C", via=hainault))
        self.assertTrue(self.bot.geodata.direct_route_exists(west_ruislip, roding_valley, "C", via=hainault, must_stop_at=wanstead))
        self.assertFalse(self.bot.geodata.direct_route_exists(snaresbrook, wanstead, "C"))
        self.assertFalse(self.bot.geodata.direct_route_exists(heathrow123, heathrow4, "P"))
        self.assertFalse(self.bot.geodata.direct_route_exists(snaresbrook, heathrow123, "All"))
        self.assertFalse(self.bot.geodata.direct_route_exists(snaresbrook, heathrow123, "C"))

        # Test direction-finding works as expected
        morden = self.bot.geodata.find_fuzzy_match("Morden", {}, RailStation)
        high_barnet = self.bot.geodata.find_fuzzy_match("High Barnet", {}, RailStation)
        self.assertTrue(self.bot.geodata.is_correct_direction("Eastbound", west_ruislip, hainault, 'C'))
        self.assertTrue(self.bot.geodata.is_correct_direction("Westbound", hainault, west_ruislip, 'C'))
        self.assertTrue(self.bot.geodata.is_correct_direction("Northbound", morden, high_barnet, 'N'))
        self.assertTrue(self.bot.geodata.is_correct_direction("Southbound", hainault, wanstead, 'C'))
        self.assertFalse(self.bot.geodata.is_correct_direction("Southbound", snaresbrook, wanstead, 'C'))
        self.assertFalse(self.bot.geodata.is_correct_direction("Southbound", morden, high_barnet, 'N'))

        # DLR Location tests
        self.assertEqual(self.bot.geodata.find_closest((51.5124, -0.0397), {}, RailStation).code, "lim")
        self.assertEqual(self.bot.geodata.find_closest((51.5124, -0.0397), {'line': 'DLR'}, RailStation).code, "lim")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Limehouse", {}, RailStation).code, "lim")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Limehouse", {'line': 'DLR'}, RailStation).code, "lim")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("Stratford Int", {}, RailStation).code, "sti")
        self.assertEqual(self.bot.geodata.find_fuzzy_match("W'wich Arsenal", {}, RailStation).code, "woa")
        self.assertIn(('West Ham', '', 'DLR'), self.bot.geodata.describe_route("Stratford", "Beckton"))
        self.assertIn(('Blackwall', '', 'DLR'), self.bot.geodata.describe_route("Stratford", "Beckton", "DLR", "Poplar"))

        limehouse = self.bot.geodata.find_fuzzy_match("Limehouse", {}, RailStation)
        beckton = self.bot.geodata.find_fuzzy_match("Beckton", {}, RailStation)
        all_saints = self.bot.geodata.find_fuzzy_match("All Saints", {}, RailStation)
        self.assertTrue(self.bot.geodata.direct_route_exists(limehouse, beckton, "DLR"))
        self.assertFalse(self.bot.geodata.direct_route_exists(limehouse, all_saints, "DLR"))
        self.assertTrue(self.bot.geodata.is_correct_direction("Eastbound", limehouse, beckton, "DLR"))
        self.assertFalse(self.bot.geodata.is_correct_direction("Eastbound", beckton, limehouse, "DLR"))

    def test_textparser(self):
        """
        Tests for the natural language parser
        """
        (line_name, origin, destination, direction) = ('Victoria', 'Sloane Square', 'Upminster', 'Eastbound')
        routes = [line_name]
        self.assertEqual(self.bot.parser.parse_message(""),                                                     (None, None, None, None))
        for route in (line_name, '%s Line' % line_name):
            self.assertEqual(self.bot.parser.parse_message("%s" % (route,)),                                    (routes, None, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s" % (route, origin)),                          (routes, origin, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s to %s" % (route, origin, destination)),       (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s from %s" % (route, origin)),                     (routes, origin, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s from %s to %s" % (route, origin, destination)),  (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s to %s" % (route, destination)),                  (routes, None, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s to %s from %s" % (route, destination, origin)),  (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s" % (route, direction)),                       (routes, None, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s %s" % (route, origin, direction)),            (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s from %s %s" % (route, origin, direction)),       (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s %s" % (route, direction, origin)),            (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s from %s" % (route, direction, origin)),       (routes, origin, None, direction))

    #
    # Request-based tests
    #

    def test_bad_line_name(self):
        """
        Test to confirm bad line names are handled OK
        """
        message = 'Xrongwoihrwg line from Oxford Circus'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'nonexistent_line', 'Xrongwoihrwg')

    def test_bad_routing(self):
        """
        Test to confirm routes that are not possible on the DLR are correctly handled
        """
        message = 'DLR from Lewisham to Woolwich Arsenal'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_direct_route', 'Lewisham', 'Woolwich Arsenal', 'DLR')

    def test_missing_station_data(self):
        """
        Test to confirm certain stations which have no data are correctly reported
        """
        message = 'Metropolitan Line from Preston Road'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'rail_station_not_in_system', 'Preston Road')

    def test_station_line_mismatch(self):
        """
        Test to confirm stations on the wrong lines, or not on the system at all, are correctly error reported
        """
        message = 'District Line from Stratford'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'rail_station_name_not_found', 'Stratford', 'District Line')
        message = 'DLR from Ealing Broadway'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'rail_station_name_not_found', 'Ealing Broadway', 'DLR')
        message = 'Wxitythr Park'
        tweet = FakeTweet(self.at_reply + message)
        network_name = self.bot.instance_name == "whensmytube" and "Tube" or "DLR"
        self._test_correct_exception_produced(tweet, 'rail_station_name_not_found', 'Wxitythr Park', network_name)

    @unittest.skipIf('--live-data' in sys.argv, "No trains unit test will fail on live data")
    def test_no_trains(self):
        """
        Test for when there are no trains at a station
        """
        # Handle when there are no trains from a station
        message = 'Waterloo & City Line from Bank'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_trains_shown', 'Waterloo & City Line', 'Bank')
        # Handle when there are no trains to a particular destination
        message = 'DLR from Lewisham to Poplar'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_trains_shown_to', 'DLR', 'Lewisham', 'Poplar')
        # Handle when there are no trains in a particular direction
        message = 'Central Line from Fairlop Westbound'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_trains_shown_in_direction', 'Westbound', 'Central Line', 'Fairlop')

    def test_no_line_specified(self):
        """
        Test for when no line is specified and it is impossible to deduce what the line is
        """
        # No direct line from Mansion House to Mornington Crescent
        message = 'Mansion House to Mornington Crescent'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_direct_route', 'Mansion House', 'Mornington Crescent', 'Tube')
        # Leicester Square has two lines, could be either
        message = 'Leicester Square'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_line_specified', 'Leicester Square')

    def test_known_problems(self):
        """
        Test known problematic inputs
        """
        # TODO Ideally, this function should be blank
        # District line from Victoria, or District & Victoria lines?
        message = 'District Victoria'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'nonexistent_line', message)
        # Not sure if "Waterloo to Bank" or "Waterloo & City Line to Bank"
        message = 'Waterloo to Bank'
        tweet = FakeTweet(self.at_reply + message)
        self._test_correct_exception_produced(tweet, 'no_geotag', message)

    def test_standard_messages(self):
        """
        Generic test for standard-issue messages
        """
        #pylint: disable=W0612
        for (line, origin_name, lat, lon, destination_name, direction, expected_origin, destination_to_avoid) in self.standard_test_data:

            # C-string format helper
            test_variables = dict([(name, eval(name)) for name in ('line', 'origin_name', 'destination_name', 'line', 'direction')])

            # 2 types of origin (geotag, name) and 3 types of destination (none, name)
            from_fragments = [value % test_variables for value in ("", " %(origin_name)s", " from %(origin_name)s")]
            to_fragments = [value % test_variables for value in ("", " to %(destination_name)s", " %(direction)s")]
            # DLR allows blank Tweets as standard
            if self.bot.username == 'whensmydlr' and line == 'DLR':
                line_fragments = [value % test_variables for value in ("%(line)s", "")]
            else:
                line_fragments = [value % test_variables for value in ("%(line)s",)]

            for from_fragment in from_fragments:
                for to_fragment in to_fragments:
                    for line_fragment in line_fragments:
                        messages = [(self.at_reply + line_fragment + from_fragment + to_fragment)]
                        if from_fragment.startswith(" from"):
                            messages.append((self.at_reply + line_fragment + to_fragment + from_fragment))
                        for message in messages:
                            if not from_fragment:
                                tweet = FakeTweet(message, (lat, lon))
                            else:
                                tweet = FakeTweet(message)
                            self._test_correct_successes(tweet, line, expected_origin, to_fragment and destination_to_avoid)


class WhensMyDLRTestCase(WhensMyTubeTestCase):
    """
    A sub-test Case for When's My DLR
    """
    def setUp(self):
        """
        Setup test
        """
        WhensMyTubeTestCase.setUp(self)
        self.bot = WhensMyTrain("whensmydlr", testing=TEST_LEVEL)
        self.at_reply = '@%s ' % self.bot.username
        self.nonstandard_test_data = (
            # Handle when there are no trains
            ('DLR from Lewisham to Poplar',
                ('Sorry! There are no DLR trains',),
                ("Lewisham [0-9]{4}",)),
        )

    #
    # Core functionality tests
    #

    def test_textparser(self):
        """
        Tests for the natural language parser
        """
        for route in ('', 'DLR'):
            (origin, destination, direction) = ('Westferry', 'All Saints', "Northbound")
            routes = route and [route] or None
            self.assertEqual(self.bot.parser.parse_message(""),                                                 (None, None, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s" % route),                                       (routes, None, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s" % (route, origin)),                          (routes, origin, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s to %s" % (route, origin, destination)),       (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s from %s" % (route, origin)),                     (routes, origin, None, None))
            self.assertEqual(self.bot.parser.parse_message("%s from %s to %s" % (route, origin, destination)),  (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s to %s" % (route, destination)),                  (routes, None, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s to %s from %s" % (route, destination, origin)),  (routes, origin, destination, None))
            self.assertEqual(self.bot.parser.parse_message("%s %s" % (route, direction)),                       (routes, None, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s %s" % (route, origin, direction)),            (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s from %s %s" % (route, origin, direction)),       (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s %s" % (route, direction, origin)),            (routes, origin, None, direction))
            self.assertEqual(self.bot.parser.parse_message("%s %s from %s" % (route, direction, origin)),       (routes, origin, None, direction))

    #
    # Request-based tests
    #

    def test_blank_tweet(self):
        """
        Override blank Tweet test as this is not needed
        """
        return

    def test_no_line_specified(self):
        """
        Override No Line Specified as this is not needed for DLR
        """
        return

    def test_known_problems(self):
        """
        No known problems specific to DLR so override with a return
        """
        return


def run_tests():
    """
    Run a suite of tests for When's My Transport
    """
    #pylint: disable=W0603
    parser = argparse.ArgumentParser(description="Unit testing for When's My Transport?")
    parser.add_argument("test_case_name", action="store", default="", help="Classname of the class to test (e.g. WhensMyBus, WhensMyTube, WhensMyDLR)")
    parser.add_argument("--remote-apis", dest="remote_apis", action="store_true", default=False, help="Test Twitter & Yahoo APIs as well")
    parser.add_argument("--live-data", dest="test_level", action="store_const", const=TESTING_TEST_LIVE_DATA, default=TESTING_TEST_LOCAL_DATA,
                        help="Test with live TfL data (may fail unpredictably!)")
    parser.add_argument("--units-only", dest="units_only", action="store_true", default=False, help="Unit tests only (overrides above)")
    test_case_name = parser.parse_args().test_case_name

    # Init tests (same for all)
    unit_tests = ('exceptions', 'geo', 'listutils', 'models', 'stringutils', 'tubeutils')
    local_tests = ('init', 'browser', 'database', 'dataparsers', 'location', 'logger', 'settings', 'textparser', 'twitter_tools')
    remote_tests = ('geocoder', 'twitter_client',)

    # Common errors for all
    format_errors = ('politeness', 'talking_to_myself', 'mention', 'blank_tweet',)
    geotag_errors = ('no_geotag', 'placeinfo_only', 'not_in_uk', 'not_in_london',)

    if test_case_name == "WhensMyBus":
        bus_errors = ('no_bus_number', 'nonexistent_bus',)
        stop_errors = ('bad_stop_id', 'stop_id_mismatch', 'stop_name_nonsense',)
        failures = format_errors + geotag_errors + bus_errors + stop_errors
        successes = ('nonstandard_messages', 'standard_messages', 'multiple_routes',)
    elif test_case_name == "WhensMyTube" or test_case_name == "WhensMyDLR":
        tube_errors = ('bad_line_name',)
        station_errors = ('bad_routing', 'missing_station_data', 'station_line_mismatch', 'no_trains', 'no_line_specified', 'known_problems')
        failures = format_errors + geotag_errors + tube_errors + station_errors
        successes = ('nonstandard_messages', 'standard_messages',)
    else:
        print "Error - %s is not a valid Test Case Name" % test_case_name
        sys.exit(1)

    if parser.parse_args().units_only:
        test_names = unit_tests
    elif parser.parse_args().remote_apis:
        test_names = unit_tests + local_tests + remote_tests + failures + successes
    else:
        test_names = unit_tests + local_tests + failures + successes

    # Sort out appropriate test level. Global variables are evil, but a necessary evil here
    # We also fail fast if using local test data, but do not if using live data (tests may just
    # fail on the fact no trains are scheduled for a stop in the test standard data)
    global TEST_LEVEL
    TEST_LEVEL = parser.parse_args().test_level
    if TEST_LEVEL == TESTING_TEST_LIVE_DATA:
        print "Testing with live TfL data"
        failfast_level = 0
    else:
        print "Testing with local test data"
        failfast_level = 1

    suite = unittest.TestSuite(map(eval(test_case_name + 'TestCase'), ['test_%s' % t for t in test_names]))
    runner = unittest.TextTestRunner(verbosity=2, failfast=failfast_level, buffer=True)
    result = runner.run(suite)
    return result.wasSuccessful()

if __name__ == "__main__":
    # run cProfile, return performance stats but only if the tests completed without failure
    prof = cProfile.Profile()
    test_succeeded = prof.runcall(run_tests)
    if test_succeeded:
        statsfile = tempfile.NamedTemporaryFile('w')
        prof.dump_stats(statsfile.name)
        stats = pstats.Stats(statsfile.name)
        stats.strip_dirs()
        stats.sort_stats('time', 'cum')
        stats.print_stats(10)
        statsfile.close()
