#!/usr/bin/env python
"""

When's My Bus?

A Twitter bot that takes requests for a bus timetable and @ replies on Twitter

e.g.

    @whensmybus 135
...will check the Tweet for its geocoded tag and work out what bus is going where

    @whensmybus 135 from 53452
...will check the Tweet for the SMS code (usually printed on a sign at the stop) and work out what bus is going where

My thanks go to Adrian Short for inspiring me to write this
http://adrianshort.co.uk/2011/09/08/open-data-for-everyday-life/

and Chris Veness for his geographic co-ordinate translation scripts
http://www.movable-type.co.uk/scripts/latlong-gridref.html

(c) 2011 Chris Applegate (chris AT qwghlm DOT co DOT uk)
Released under the MIT License

TODO
 - Fix "Gil's bug"
 - Add lookup by geocoding English
 - Better database performance
 - Consistent Virtual Bus Stop use (or just delete them?)
"""
# Standard libraries of Python 2.6
import ConfigParser
import json
import logging
import logging.handlers as logging_handlers
import os
import re
import sqlite3
import string
import sys
import time
import urllib2
from pprint import pprint # For debugging

# Tweepy is available https://github.com/tweepy/tweepy
import tweepy

# Functions from our file geotools.py
from geotools import LatLongToOSGrid, convertWGS84toOSGB36, gridrefNumToLet

# Some constants we use
VERSION_NUMBER = 0.20
TFL_API_URL = "http://countdown.tfl.gov.uk/stopBoard/%s"
WHENSMYBUS_HOME = os.path.dirname(os.path.abspath(__file__))

class WhensMyBusException(Exception):
    """
    Exception we use to signal send an error to the user
    """
    # Possible id => message pairings, so we can use a shortcode to summon a much more explanatory message
    # Why do we not just send the full string as a parameter to the Exception? Mainly so we can unit test (see testing.py)
    # but also as it saves duplicating string for similar errors (e.g. when TfL service is down)
    exception_values = {
        'blank_tweet'     : "I need to have a bus number in order to find the times for it",
        'nonexistent_bus' : "I couldn't recognise the number you gave me (%s) as a London bus",
        'placeinfo_only'  : "The Place info on your Tweet isn't precise enough to find nearest bus stop. Try again with a GPS-enabled device",
        'no_geotag'       : "Your Tweet wasn't geotagged. Please make sure you're using a GPS-enabled device & location is enabled on your Tweet",
        'bad_stop_id'     : "I couldn't recognise the number you gave me (%s) as a valid bus stop ID",
        'stop_id_mismatch': "That bus (%s) does not appear to stop at that stop (%s)",
        'stop_not_found'  : "I couldn't find any bus stops on your route by that name (%s)",
        'not_in_uk'       : "You do not appear to be located in the United Kingdom",
        'not_in_london'   : "You do not appear to be located in the London Buses area",
        'no_stops_nearby' : "I could not find any stops near you",
        'tfl_server_down' : "I can't access TfL's servers right now - they appear to be down :(",
    }

    def __init__(self, msgid, *string_params):
        """
        Fetch a message with the ID from the dictionary above
        String formatting params optional, only needed if there is C string formatting in the error message
        e.g. WhensMyBusException('nonexistent_bus', '214')
        """
        value = WhensMyBusException.exception_values.get(msgid, '') % string_params
        super(WhensMyBusException, self).__init__(value)
        logging.debug("Application exception encountered: %s", value)
        self.value = value[:115]
        
    def __str__(self):
        return repr(self.value)

class WhensMyBus:
    """
    Main class devoted to checking for Tweets and replying to them. Instantiate with no variables
    (all config is done in the file whensmybus.cfg) and then call check_tweets()
    """
    def __init__(self, testing=None, silent=False):

        try:
            open(WHENSMYBUS_HOME + '/whensmybus.cfg')
            config = ConfigParser.SafeConfigParser({ 'test_mode' : False, 'debug_level' : 'INFO' })
            config.read(WHENSMYBUS_HOME + '/whensmybus.cfg')
        except (ConfigParser.Error, IOError):
            print "Fatal error: can't find a valid config file. Please make sure there is a whensmybus.cfg file in this directory"
            sys.exit(1)

        # Set up some logging
        if len(logging.getLogger('').handlers) == 0:
            logging.basicConfig(level=logging.DEBUG, filename=os.devnull)

            # Set up some basic logging to stdout that shows info or debug level depending on user config
            if silent:
                console_output = open(os.devnull, 'w')
            else:
                console_output = sys.stdout
            
            console = logging.StreamHandler(console_output)
            console.setLevel(logging.__dict__[config.get('whensmybus', 'debug_level')])
            console.setFormatter(logging.Formatter('%(message)s'))

            # Set up some proper logging to file that catches debugs
            logfile = os.path.abspath(WHENSMYBUS_HOME + '/logs/whensmybus.log')
            rotator = logging_handlers.RotatingFileHandler(logfile, maxBytes=256*1024, backupCount=99)
            rotator.setLevel(logging.DEBUG)
            rotator.setFormatter(logging.Formatter('%(asctime)s %(levelname)-8s %(message)s'))

            logging.getLogger('').addHandler(console)
            logging.getLogger('').addHandler(rotator)
            logging.debug("Initializing...")

        if testing != None:
            self.testing = testing
        else:
            self.testing = config.get('whensmybus', 'test_mode')
        
        if self.testing:
            logging.info("In TEST MODE - No Tweets will be made!")

        # Load up the databases - one for the geodata, and one used a generic settings
        (_notused, self.geodata) = load_database('whensmybus.geodata.db')
        (self.settingsdb, self.settings) = load_database('whensmybus.settings.db')
        self.settings.execute("create table if not exists whensmybus_settings (setting_name unique, setting_value)")
        self.settingsdb.commit()

        # That which fetches the JSON
        self.opener = urllib2.build_opener()
        self.opener.addheaders = [('User-agent', 'When\'s My Bus? v. %s' % VERSION_NUMBER),
                                  ('Accept','text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8')]
        
        # OAuth on Twitter
        self.username = config.get('whensmybus','username')
        
        logging.debug("Authenticating with Twitter")
        consumer_key = config.get('whensmybus','consumer_key')
        consumer_secret = config.get('whensmybus','consumer_secret')
        key = config.get('whensmybus','key')
        secret = config.get('whensmybus','secret')

        auth = tweepy.OAuthHandler(consumer_key, consumer_secret)
        auth.set_access_token(key, secret)        
        self.api = tweepy.API(auth)

        # This used to verify credentials, but it used up a valuable API call, so it's now disabled
        # if not self.api.verify_credentials():
            # logging.error("Error: OAuth connection to Twitter failed, probably due to an invalid token")
            # sys.exit(1)

    
    def get_setting(self, setting_name):
        """
        Simple wrapper to fetch value of setting from settings database
        """
        self.settings.execute("select setting_value from whensmybus_settings where setting_name = '%s'" % setting_name)
        row = self.settings.fetchone()
        return row and row[0]

    def update_setting(self, setting_name, setting_value):
        """
        Simple wrapper to set value of named setting in settings database
        """
        self.settings.execute("insert or replace into whensmybus_settings (setting_name, setting_value) values ('%s', '%s')" % (setting_name, setting_value))
        self.settingsdb.commit()
        
    def check_tweets(self):
        """
        Check Tweets that are replies to us
        """
        # Check For @ reply Tweets
        last_answered_tweet = self.get_setting('last_answered_tweet')
        try:
            # Rotates through pages if lots of replies
            if self.testing:
                tweets = tweepy.Cursor(self.api.mentions, since_id=last_answered_tweet).items(5)
            else:
                tweets = tweepy.Cursor(self.api.mentions, since_id=last_answered_tweet).items()
                
        # This is most likely to fail if OAuth is not correctly set up
        except tweepy.error.TweepError:
            logging.error("Error: OAuth connection to Twitter failed, probably due to an invalid token")
            sys.exit(1)
        
        # Convert iterator to array so we can reverse it
        tweets = [tweet for tweet in tweets][::-1]
        # No need to bother if no replies
        if not tweets:
            logging.info("No new Tweets, exiting...")
        else:
            logging.info("%s replies received!" , len(tweets))
            
        # Alright! Let's get going
        for tweet in tweets:
            try:
                replies = self.process_tweet(tweet)
            # Handler for any of the many possible reasons that this could go wrong
            except WhensMyBusException as exc:
                logging.debug("Exception encountered: %s" , exc.value)
                replies = ("@%s Sorry! %s" % (tweet.user.screen_name, exc.value),)

            if not replies:
                continue
            
            for reply in replies:
                logging.info("Replying back to user with: %s", reply)

            # Reply back to the user, if not in testing mode
            if not self.testing:
                try:
                    for reply in replies:                    
                        self.api.update_status(status=reply, in_reply_to_status_id=tweet.id)
                    # So we can keep track of our since variable
                    self.update_setting('last_answered_tweet', tweet.id)
                # This catches any errors, most typically if we send multiple Tweets to the same person with the same error
                # In which case, not much we can do
                except tweepy.error.TweepError:
                    continue

        # Keep an eye on our rate limit, for science
        self.report_twitter_limit_status()        
        
        
    def process_tweet(self, tweet):
        """
        Processes a single Tweet object and returns a list of replies to be sent back to that user        
        """
        username = tweet.user.screen_name
        message = tweet.text
        logging.info("Have a message from %s: %s", username, message)

        # Don't start talking to yourself
        if username == self.username:
            logging.debug("Not talking to myself, that way madness lies")
            return ()
            
        # Get route number, from and to from the message
        (route_number, origin, destination) = self.parse_message(message)
        # If no number found at all, just skip
        if route_number == None:
            return ()
            
        # Not all valid-looking bus numbers are real bus numbers (e.g. 214, RV11) so we check database to make sure
        self.geodata.execute("SELECT * FROM routes WHERE Route=?", (route_number,))
        if not len(self.geodata.fetchall()):
            raise WhensMyBusException('nonexistent_bus', route_number)

        # If no origin specified, let's see if we have co-ordinates on the Tweet
        if origin == None:
            if tweet.coordinates:
                logging.debug("Detect geolocation on Tweet, locating stops")
                # Twitter gives latitude then longitude, so need to reverse this
                position = tweet.coordinates['coordinates'][::-1]
                relevant_stops = self.get_stops_by_geolocation(route_number, position)
                
            # Some people (especially Tweetdeck users) add a Place on the Tweet, but not an accurate enough long & lat
            elif tweet.place:
                raise WhensMyBusException('placeinfo_only')
            
            # If there's no geoinformation at all then say so
            else:
                raise WhensMyBusException('no_geotag')
        
        else:
            # Try to see if origin is a bus stop ID
            match = re.match('^[0-9]{5}$', origin)
            if match:
                relevant_stops = self.get_stops_by_stop_number(route_number, origin)
            else:
                relevant_stops = self.get_stops_by_origin_name(route_number, origin)
        
        # If the above has found stops on this route
        if relevant_stops:
        
            # In due course, we would filter the stops by the destination specified :)
            
            time_info = self.get_departure_data(relevant_stops, route_number)
            reply = "@%s %s %s" % (username, route_number, "; ".join(time_info))
        else:
            raise WhensMyBusException('stop_not_found')
        
        # Max lead to a Tweet is 22 chars max (@ + 15 letter usename + space + 4-digit bus + space)
        # Longest stop name is HANWORTH AIR PARK LEISURE CENTRE & LIBRARY = 42 
        #
        # Longest stop name (42) + " to " + Longest terminus name (15) + space + 4-digit time + semi-colon = 67 
        #
        # So at the moment highest possible length of a single route is 67 and so longest possible Tweet is:
        # 22 + 67 + 66 = 155 characters
    
        if len(reply) > 140:
            replies = reply.split("; ", 2)
            replies[0] = "%s..." % replies[0]
            replies[1] = "@%s ...%s" % (username, replies[1])
        else:
            replies = (reply,)
            
        return tuple(replies)

    def parse_message(self, message):
        """
        Parse a message, but do not attempt to attain semantic meaning behind data   
        
        Message is of format: "@whensmybus route_number [from origin] [to destination]"
        """
        # Ignore mentions that are not direct replies
        if not message.lower().startswith('@%s' % self.username.lower()):
            logging.debug("Not a proper @ reply, skipping")
            return (None, None, None)
        
        # Remove hashtags and @username
        message = re.sub(' +#\w+ ?', '', message)
        message = message[len('@%s ' % self.username):].lstrip()
        if not message:
            raise WhensMyBusException('blank_tweet')

        # Extract a route number out of the first word by using the regexp for a London bus (0-2 letters then 1-3 numbers)
        match = re.match('^([A-Z]{0,2}[0-9]{1,3})(.*)$', message, re.I)
        # If we can't find a number, it's most likely the person was saying "Thank you" so just skip replying entirely 
        if not match:
            logging.debug("@ reply didn't contain a valid-looking bus number, skipping")
            return (None, None, None)
        
        # In case the user has used lowercase letters, fix that (e.g. d3)
        route_number = match.group(1).upper()

        # Work backwards from end of remainder to get destination, then origin
        origin, destination = None, None
        remainder = match.group(2)
        match = re.search('( +to +(.*)$)', remainder, re.I)
        destination = match and match.group(2)
        if match:
            remainder = remainder[:-1 * len(match.group(1))]

        match = re.search('( +from +(.*)$)', remainder, re.I)
        origin = match and match.group(2)
            
        return (route_number, origin, destination)
        
    def get_stops_by_geolocation(self, route_number, position):
        """
        Takes a route number and lat/lng and works out closest bus stops in each direction
        """
        # GPSes use WGS84 model of Globe, but Easting/Northing based on OSGB36, so convert
        logging.debug("Position in WGS84 determined as: %s %s", position[0], position[1])
        position = convertWGS84toOSGB36(*position)
        logging.debug("Converted to OSGB36: %s %s", position[0], position[1])

        # Turn it into an Easting/Northing
        easting, northing = LatLongToOSGrid(position[0], position[1])
        gridref = gridrefNumToLet(easting, northing)
        
        # Grid reference provides us an easy way with checking to see if in the UK - it returns blank string if not in UK bounds
        if not gridref:
            raise WhensMyBusException('not_in_uk')
        # Grids TQ and TL cover London, SU is actually west of the M25 but the 81 travels to Slough
        elif gridref[:2] not in ('TQ', 'TL', 'SU'):
            raise WhensMyBusException('not_in_london')            
        else:
            logging.debug("Translated into OS Easting %s, Northing %s", easting, northing)
            logging.debug("Translated into Grid Reference %s", gridref)

        # A route typically has two "runs" (e.g. one eastbound, one west) but some have more than that, so work out the runs
        self.geodata.execute("SELECT MAX(Run) FROM routes WHERE Route='%s'" % route_number)
        max_runs = int(self.geodata.fetchone()[0])
        
        relevant_stops = []
        for run in range(1, max_runs+1):
        
            # Do a funny bit of Pythagoras to work out closest stop. We can't find square root of a number in sqlite
            # but then again, we don't need to, the smallest square will do. Sort by this column in ascending order
            # and find the first row
            #
            # Also note the join from the routes table to locations table on the index Stop_Code_LBSL, and how we avoid
            # TfL's "Virtual_Bus_Stop" (used for providing waypoints that buses don't stop at)
            query = """
                    SELECT (locations.Location_Easting - %d)*(locations.Location_Easting - %d) + (locations.Location_Northing - %d)*(locations.Location_Northing - %d) AS dist_squared,
                          routes.Run,
                          locations.Heading,
                          locations.Sms_Code,
                          locations.Stop_Name
                    FROM routes
                    JOIN locations ON routes.Stop_Code_LBSL = locations.Stop_Code_LBSL
                    WHERE Route='%s' AND Run='%s' AND routes.Virtual_Bus_Stop = '0' 
                    ORDER BY dist_squared
                    LIMIT 1
                    """ % (easting, easting, northing, northing, route_number, run)
    
            # Note we fetch the Sms_code not the Stop_Code_LBSL value out of this row - this is the ID used
            # in TfL's system
            self.geodata.execute(query)
            row = self.geodata.fetchone()
            relevant_stops.append([row[key] for key in ('Stop_Name', 'Sms_Code', 'Run', 'Heading')])
        
        if relevant_stops:
            logging.debug("Have found stop numbers: %s", ', '.join([s[1] for s in relevant_stops]))
            return relevant_stops
        else:
            # This may well never be raised - there will always be a nearest stop on a route for someone, even if it is 1000km away
            raise WhensMyBusException('no_stops_nearby')
            
    def get_stops_by_stop_number(self, route_number, stop_number):
        """
        Returns a list of stops (should be length 1) that has SMS ID of stop_number
        """
        # Pull the ID out of the locations database and see if it exists
        self.geodata.execute("SELECT * FROM locations WHERE Sms_Code=? AND Virtual_Bus_Stop=?", (stop_number, '0'))
        location = self.geodata.fetchone()
    
        if location:
            # Check that the stop with that ID is on the route that we want
            self.geodata.execute("SELECT * FROM routes WHERE Stop_Code_LBSL=? AND Route=?", (location['Stop_Code_LBSL'], route_number))
            route = self.geodata.fetchone()
            # If so then let's get the name, location, run & heading for that route
            if route:
                relevant_stops = ([location['Stop_Name'], location['Sms_Code'], route['Run'], location['Heading']],)
                return relevant_stops
            else:
                raise WhensMyBusException('stop_id_mismatch', route_number, stop_number)
        else:
            raise WhensMyBusException('bad_stop_id', stop_number)

    def get_stops_by_origin_name(self, route_number, origin):
        """
        Tries to get relevant stops by the placename of the origin
        """
        # Try to get an exact match in database
        relevant_stops = []
        self.geodata.execute("SELECT * FROM routes WHERE Route=? AND Virtual_Bus_Stop=?", (route_number, '0'))
        rows = self.geodata.fetchall()
        for route in rows:
            normalised_name = route['Stop_Name']
            for unwanted in ('<>', '#', '[DLR]', '>T<', ' '):
                normalised_name = normalised_name.replace(unwanted, '')
            if (normalised_name == origin.upper()):
                self.geodata.execute("SELECT * FROM locations WHERE Stop_Code_LBSL=?", route['Stop_Code_LBSL'])
                location = self.geodata.fetchone()
                relevant_stops.append([location['Stop_Name'], location['Sms_Code'], route['Run'], location['Heading']],)
                
        if not relevant_stops:
            # Eventually geocoding would go here
            raise WhensMyBusException('stop_not_found', origin)
            
        return relevant_stops
            
    def get_departure_data(self, relevant_stops, route_number):
        """
        Function that fetches the JSON data from the TfL website, for a list of relevant_stops 
        and a particular route_number, and returns the time(s) of buses on that route serving
        that stop(s)
        """
        time_info = []

        # Values in tuple correspond to what was added in relevant_stops.append() above
        for (stop_name, stop_number, run, heading) in relevant_stops:
        
            # Get rid of TfL's ASCII symbols for Tube, National Rail, DLR & Tram
            for unwanted in ('<>', '#', '[DLR]', '>T<'):
                stop_name = stop_name.replace(unwanted, '')
            stop_name = string.capwords(stop_name.strip())
        
            tfl_url = TFL_API_URL % stop_number
            logging.debug("Getting %s", tfl_url)
    
            try:
                response = self.opener.open(tfl_url)
                json_data = response.read()
    
            # Handle browsing error
            except urllib2.HTTPError, exc:
                logging.error("HTTP Error %s reading %s, aborting", exc.code, tfl_url)
                raise WhensMyBusException('tfl_server_down')
            except Exception, exc:
                logging.error("%s (%s) encountered for %s, aborting", exc.__class__.__name__, exc, tfl_url)
                raise WhensMyBusException('tfl_server_down')
    
            # Try to parse this as JSON
            if json_data:
                try:
                    bus_data = json.loads(json_data)
                    arrivals = bus_data.get('arrivals', [])
                    
                    if not arrivals:
                        # Handle TfL's JSON-encoded error message
                        if bus_data.get('stopBoardMessage', '') == "noPredictionsDueToSystemError":
                            raise WhensMyBusException('tfl_server_down')
                        else:
                            logging.error("No arrival data for this stop right now")
                    else:
                        # Do the user a favour - check for both number and possible Night Bus version of the bus
                        relevant_arrivals = [a for a in arrivals if (a['routeName'] == route_number or a['routeName'] == 'N' + route_number)
                                                                    and a['isRealTime']
                                                                    and not a['isCancelled']]

                        if relevant_arrivals:
                            # Get the first arrival for now
                            arrival = relevant_arrivals[0]
                            # Every character counts! :)
                            scheduled_time =  arrival['scheduledTime'].replace(':', '')
                            
                            # Short hack to get BST working
                            if time.daylight:
                                hour = (int(scheduled_time[0:2]) + 1) % 24
                                scheduled_time = '%02d%s' % (hour, scheduled_time[2:4])
                                
                            logging.debug("Just out of interest, the destination is %s with length %s", arrival['destination'], len(arrival['destination']))
                                
                            time_info.append("%s to %s %s" % (stop_name, arrival['destination'], scheduled_time))
                        else:
                            time_info.append("%s: None shown going %s" % (stop_name, heading_to_direction(heading)))

                # Probably a 503 Error message in HTML if the JSON parser is choking and raises a ValueError
                except ValueError, exc:
                    logging.error("%s encountered when parsing %s - likely not JSON!", exc, tfl_url)
                    raise WhensMyBusException('tfl_server_down')        

        # If the number of runs is 3 or 4, get rid of any "None shown"
        if len(time_info) > 2:
            logging.debug("Number of runs is %s, removing any non-existent entries" , len(time_info))
            time_info = [t for t in time_info if t.find("None shown") == -1]

        return time_info

    def report_twitter_limit_status(self):
        """
        Helper function to tell us what our Twitter API hit count & limit is
        """
        status_json = self.api.rate_limit_status()
        logging.debug("I have %s out of %s hits remaining this hour", status_json['remaining_hits'], status_json['hourly_limit'])
        logging.debug("Next reset time is %s", (status_json['reset_time']))

# Helper functions

def load_database(dbfilename):
    """
    Helper function to load a database and return links to it and its cursor
    """
    logging.debug("Opening database %s", dbfilename)
    dbs = sqlite3.connect(WHENSMYBUS_HOME + '/db/' + dbfilename)
    dbs.row_factory = sqlite3.Row
    return (dbs, dbs.cursor())
    
def heading_to_direction(heading):
    """
    Helper function to convert a bus stop's heading (in degrees) to human-readable direction
    """
    dirs = ('North', 'NE', 'East', 'SE', 'South', 'SW', 'West', 'NW')
    # North lies between -22 and +22, NE between 23 and 67, East between 68 and 112, etc 
    i = ((int(heading)+22)%360)/45
    return dirs[i]
    
if __name__ == "__main__":
    WMB = WhensMyBus()
    WMB.check_tweets()
    