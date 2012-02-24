#!/usr/bin/env python
"""
Module containing custom exceptions for WhensMyTransport
"""
import logging

class WhensMyTransportException(Exception):
    """
    Exception we use to signal send an error to the user
    """
    # Possible id => message pairings, so we can use a shortcode to summon a much more explanatory message
    # Why do we not just send the full string as a parameter to the Exception? Mainly so we can unit test (see testing.py)
    # but also as it saves duplicating string for similar errors (e.g. when TfL service is down)
    #
    # Error message should be no longer than 115 chars so we can put a username and the word Sorry and still be under 140
    #
    # A fatal error is one that ends the entire query (i.e. it is not possible to find any bus given the user's
    # query). A non-fatal error is one for a particular route, but if the user has asked for other routes then they may
    # still work)
    exception_values = {
    
        # Fatal errors common to all instances
        'placeinfo_only'  : "The Place info on your Tweet isn't precise enough http://bit.ly/rCbVmP Please enable GPS, or say '%s from <place>'",
        'no_geotag'       : "Your Tweet wasn't geotagged. Please enable GPS, or say '%s from <placename>' http://bit.ly/sJbgBe",
        'dms_not_taggable': "Direct messages can't use geotagging. Please send your message in the format '%s from <placename>'",
        'not_in_uk'       : "You do not appear to be located in the United Kingdom",    
        'not_in_london'   : "You do not appear to be located in the London area", 
        'unknown_error'   : "An unknown error occurred processing your Tweet. My creator has been informed",
        
        # WhensMyBus fatal errors
        'blank_bus_tweet' : "I need to have a bus number in order to find the times for it",
        'bad_stop_id'     : "I couldn't recognise the number you gave me (%s) as a valid bus stop ID",
        'tfl_server_down' : "I can't access TfL's servers right now - they appear to be down :(",
        
        # WhensMyBus non-fatal errors
        'nonexistent_bus' : "I couldn't recognise the number you gave me (%s) as a London bus",     
        'stop_name_not_found' : "I couldn't find any bus stops on the %s route by that name (%s)",
        'stop_id_not_found'  : "The %s route doesn't call at the stop with ID %s",
        'no_bus_arrival_data' : "There aren't any %s buses currently shown for your stop",

        # WhensMyTube & WhensMyDLR errors
        'rail_station_name_not_found' : "I couldn't recognise that station (%s) as being on the %s",
        'no_rail_arrival_data' : "There aren't any %s trains currently shown for %s station",

        # WhensMyTube-only errors
        'blank_tube_tweet': "I need to have a Tube line in order to find the times for it",
        'nonexistent_line' : "I couldn't recognise that line (%s) as a Tube line",
        'tube_station_not_in_system' : "TfL don't provide live departure data for %s station :(",
        'tube_station_closed' : "%s station is currently closed %s",
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
        self.value = value[:115]

    def __str__(self):
        """
        Return a string representation of this Exception
        """
        return repr(self.value)
