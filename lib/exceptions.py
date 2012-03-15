#!/usr/bin/env python
"""
Module containing custom exceptions for WhensMyTransport
"""
import logging


class WhensMyTransportException(Exception):
    """
    Exception we use to signal send a WhensMyTransport-specific error to the user
    """
    # Possible id => message pairings, so we can use a shortcode to summon a much more explanatory message
    # Why do we not just send the full string as a parameter to the Exception? Mainly so we can unit test (see testing.py)
    # but also as it saves duplicating string for similar errors (e.g. when TfL service is down)
    #
    # A fatal error is one that ends the entire query (i.e. it is not possible to find any bus given the user's
    # query). A non-fatal error is one for a particular route, but if the user has asked for other routes then they may
    # still work)
    exception_values = {

        # Fatal errors common to all instances
        'placeinfo_only':   "The Place info on your Tweet isn't precise enough http://bit.ly/rCbVmP Please enable GPS, or say '%s from <place>'",
        'no_geotag':        "Your Tweet wasn't geotagged. Please enable GPS, or say '%s from <placename>' http://bit.ly/sJbgBe",
        'dms_not_taggable': "Direct messages can't use geotagging. Please send your message in the format '%s from <placename>'",
        'not_in_uk':        "You do not appear to be located in the United Kingdom",
        'not_in_london':    "You do not appear to be located in the London area",
        'unknown_error':    "An unknown error occurred processing your Tweet. My creator has been informed",

        # WhensMyBus fatal errors
        'blank_bus_tweet':  "I need to have a bus number in order to find the times for it",
        'bad_stop_id':      "I couldn't recognise the number you gave me (%s) as a valid bus stop ID",
        'tfl_server_down':  "I can't access TfL's servers right now - they appear to be down :(",

        # WhensMyBus non-fatal errors
        'nonexistent_bus':     "I couldn't recognise the number you gave me (%s) as a London bus",
        'stop_name_not_found': "I couldn't find any bus stops on the %s route by that name (%s)",
        'stop_id_not_found':   "The %s route doesn't call at the stop with ID %s",
        'no_buses_shown':      "There are no %s buses currently shown from your stop",
        'no_buses_shown_to':   "There are no %s buses currently shown from your stop to %s",

        # WhensMyTube & WhensMyDLR errors
        'rail_station_name_not_found': "I couldn't recognise that station (%s) as being on the %s",
        'no_trains_shown':           "There are no %s trains currently shown going from %s",
        'no_trains_shown_to':        "There are no %s trains currently shown going from %s to %s",
        'no_direct_route':           "There is no direct route between %s and %s on the %s",

        # WhensMyTube-only errors
        'blank_tube_tweet':           "I need to have a Tube line in order to find the times for it",
        'nonexistent_line':           "I couldn't recognise that line (%s) as a Tube line",
        'rail_station_not_in_system': "TfL don't provide live departure data for %s station :(",
        'tube_station_closed':        "%s station is currently closed %s",
    }

    def __init__(self, msgid='unknown_error', *string_params):
        """
        Fetch a message with the ID from the dictionary above
        String formatting params optional, only needed if there is C string formatting in the error message
        e.g. WhensMyTransportException('nonexistent_bus', '214')
        """
        value = WhensMyTransportException.exception_values.get(msgid, '') % string_params
        super(WhensMyTransportException, self).__init__(value)
        logging.debug("Application exception encountered: %s", value)
        self.msgid = msgid
        self.value = value

    def __str__(self):
        """
        Return a string representation of this Exception
        """
        return repr(self.value)

    def get_user_message(self):
        """
        Returns a string representation of this exception for use in a Twitter message. 115 characters allows space for Sorry and @username
        """
        logging.debug("Returning exception to user: %s", self.value[:115])
        return "Sorry! %s" % self.value[:115]

