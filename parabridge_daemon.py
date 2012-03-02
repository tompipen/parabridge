#!/usr/bin/env python
# coding:utf-8 vi:et:ts=2

import sys
import argparse
import threading
import time
import socket
import os
import json
import re
import sqlite3
from SimpleXMLRPCServer import SimpleXMLRPCServer
from settings import Settings

##  Allow to import packages from 'vendor' subfolder.
##! Put first so it |pyparadox| is installed, it is taken from |vendor|.
sys.path.insert( 0, '{0}/vendor'.format( sys.path[ 0 ] ) )

import pyparadox

class Worker( threading.Thread ) :

  m_oInstance = None

  def __init__( self ) :
    super( Worker, self ).__init__()
    self.m_fShutdown = False
    self.m_oShutdown = threading.Event()
    self.m_fCfgChanged = True
    self.m_mResults = {}
    self.m_oTimeReloadLast = None

  def run( self ) :
    while not self.m_fShutdown :
      if self.m_fCfgChanged :
        lTasks = Settings.taskList()
        self.m_fCfgChanged = False
        self.m_oTimeReloadLast = time.localtime()
      for mTask in lTasks :
        sSrc = os.path.expanduser( mTask[ 'src' ] )
        sDst = os.path.expanduser( mTask[ 'dst' ] )
        self.processTask( mTask[ 'guid' ], mTask[ 'name' ], sSrc, sDst )
      time.sleep( 1 )

  def processTask( self, i_sGuid, i_sName, i_sSrc, i_sDst ) :
    def setRes( i_sTxt ) :
      self.m_mResults[ i_sName ] = i_sTxt
      return False
    if not os.path.exists( i_sSrc ) :
      return setRes( "Path \"{0}\" not found.".format( i_sSrc ) )
    if not os.path.isdir( i_sSrc ) :
      return setRes( "Path \"{0}\" is not a directory.".format( i_sSrc ) )
    try :
      os.makedirs( os.path.dirname( i_sDst ) )
    except OSError :
      pass
    lSrcFiles = [ i_sSrc + os.sep + s for s in os.listdir( i_sSrc ) ]
    lSrcFiles = [ s for s in lSrcFiles if os.path.isfile( s ) ]
    lSrcFiles = [ s for s in lSrcFiles if re.search( "(?i)\.db$", s ) ]
    if 0 == len( lSrcFiles ) :
      return setRes( "No .db files in \"{0}\".".format( i_sSrc ) )
    lProcessed = []
    nTotal = len( lSrcFiles )
    with sqlite3.connect( i_sDst ) as oConn :
      for i, sSrcFile in enumerate( lSrcFiles ) :
        setRes( "Processing {0}/{1}".format( i + 1, nTotal ) )
        if self.processParadoxFile( i_sGuid, sSrcFile, oConn ) :
          if self.m_fShutdown :
            return
          lProcessed.append( True )
    sTime = time.strftime( '%Y.%m.%d %H:%M:%S' )
    nProcessed = len( lProcessed )
    setRes( "Processed {0}/{1} at {2}.".format( nProcessed, nTotal, sTime ) )

  ##x Process individual Paradox |.db| file and synchronize specified
  ##  SQLite database file with it.
  def processParadoxFile( self, i_sGuid, i_sSrc, i_oConn ) :
    try :
      sFile = os.path.basename( i_sSrc )
      nIndexLast = Settings.indexLastGet( i_sGuid, sFile )
      mArgs = { 'shutdown' : self.m_oShutdown }
      ##  First time parse of this file?
      if nIndexLast is None :
        oDb = pyparadox.open( i_sSrc, ** mArgs )
      else :
        mArgs[ 'start' ] = nIndexLast + 1
        oDb = pyparadox.open( i_sSrc, ** mArgs )
      ##  We can handle only tables that has autoincrement field (if
      ##  such field exists, it will be first for Paradox database. We
      ##  need it to detect updates).
      if len( oDb.fields ) < 1 or not oDb.fields[ 0 ].IsAutoincrement() :
        return
      ##  Table empty or not updated since saved last index.
      if 0 == len( oDb.records ) :
        return
      for oRecord in oDb.records :
        nIndex = oRecord.fields[ 0 ]
        if nIndexLast is not None and nIndexLast >= nIndex :
          raise Exception( "Consistency error." )
        nIndexLast = nIndex
        self.processParadoxRecord( oDb, oRecord, i_oConn )
      Settings.indexLastSet( i_sGuid, sFile, nIndexLast )
    except pyparadox.Shutdown :
      return False
    return True

  def processParadoxRecord( self, i_oDb, i_oRecord, i_oConn ) :
    sTableName = i_oDb.table_name

  def shutdown( self ) :
    self.m_fShutdown = True
    ##! After |m_fShutdown| is set to prevent races.
    self.m_oShutdown.set()

  @classmethod
  def instance( self ) :
    if not self.m_oInstance :
      self.m_oInstance = Worker()
    return self.m_oInstance

  def cfgChanged( self ) : self.m_fCfgChanged = True

  def results( self ) : return self.m_mResults

  def timeReloadLast( self ) : return self.m_oTimeReloadLast

class Server( SimpleXMLRPCServer, object ) :

  def __init__( self, i_nPort ) :
    gAddr = ( 'localhost', i_nPort )
    super( Server, self ).__init__( gAddr, logRequests = False )
    self.fShutdown = False
    self.register_function( self.stop )
    self.register_function( self.status )
    self.register_function( self.cfg_changed )

  def serve_forever( self ) :
    while not self.fShutdown :
      self.handle_request()

  def stop( self ) :
    self.fShutdown = True
    return True

  def status( self ) :
    oTimeReloadLast = Worker.instance().timeReloadLast()
    sMsg = """Daemon is running.
      \tConfiguration reloaded: {0}""".format(
      time.strftime( '%Y.%m.%d %H:%M:%S', oTimeReloadLast ) )
    mResults = Worker.instance().results()
    for sKey in sorted( mResults.keys() ) :
      sMsg += "\n{0}:\n\t {1}".format( sKey, mResults[ sKey ] )
    return re.sub( '\t', ' ', re.sub( ' +', ' ', sMsg ) )

  def cfg_changed( self ) :
    Worker.instance().cfgChanged()
    return True

Settings.init()
oParser = argparse.ArgumentParser( description = "Parabridge daemon" )
oParser.add_argument( 'port', type = int, help = "Port to listen on" )
oArgs = oParser.parse_args()

Worker.instance().start()
try :
  Server( oArgs.port ).serve_forever()
except socket.error :
  ##  Unable to bind to port if already started.
  pass
finally :
  Worker.instance().shutdown()

