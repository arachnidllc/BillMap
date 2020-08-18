#!/usr/bin/env python3
#
# Command line template from https://gist.githubusercontent.com/opie4624/3896526/raw/3aff2ad7030a74ce26f9fcf80791ae0396d84f18/commandline.py

import sys, os, argparse, logging, re, json, gzip
import datetime
from typing import Dict
from functools import reduce

PATH_TO_BILLS_META = '../billsMeta.json'
SAVE_ON_COUNT = 1000

BILL_TYPES = {
  'ih': 'introduced',
  'rh': 'reported to house'
}

CURRENT_CONGRESSIONAL_YEAR = datetime.date.today().year if datetime.date.today() > datetime.date(datetime.date.today().year, 1, 3) else (datetime.date.today().year - 1)
CURRENT_CONGRESS, cs_temp = divmod(round(((datetime.date(CURRENT_CONGRESSIONAL_YEAR, 1, 3) - datetime.date(1788, 1, 3)).days) / 365) + 1, 2)
CURRENT_SESSION = cs_temp + 1

logging.basicConfig(filename='billdata.log', filemode='w', level='INFO')
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler(sys.stdout))

def logName(dirName: str, fileName: str):
  """
  Prints the name provided (path to a file to be processed) to the log.

  Args:
      fname (str): path of file to be processed 
  """

  logger.info('Processing: \t%s' % fileName)

def getTopBillLevel(dirName: str):
  """
  Get path for the top level of a bill, e.g. ../congress/data/116/bills/hr/hr1

  Args:
      dirName (str): path to match 

  Returns:
      [bool]: True if path is a top level (which will contain data.json); False otherwise  
  """
  dirName_parts = dirName.split('/')
  return (re.match(r'[a-z]+[0-9]+', dirName_parts[-1]) is not None and dirName_parts[-3]=='bills')

def isDataJson(fileName: str) -> bool:
  return fileName == 'data.json'

def walkBillDirs(rootDir = '../congress/data', processFile = logName, dirMatch = getTopBillLevel, fileMatch = isDataJson):
    for dirName, subdirList, fileList in os.walk(rootDir):
      if dirMatch(dirName):
        logger.info('Entering directory: %s' % dirName)
        filteredFileList = [fitem for fitem in fileList if fileMatch(fitem)]
        for fname in filteredFileList:
            processFile(dirName=dirName, fileName=fname)

def deep_get(dictionary: Dict, *keys):
  """
  A Dict utility to get a field; returns None if the field does not exist

  Args:
      dictionary (Dict): an arbitrary dictionary 

  Returns:
      any: value of the specified key, or None if the field does not exist
  """

  return reduce(
    lambda d, key: d.get(key, None) if isinstance(d, dict) else None, keys, 
    dictionary)

def loadJSON(filePath: str):
  with open(filePath, 'rb') as f:
    fileDict = json.load(f)
  return fileDict

def getBillCongressTypeNumber(fileDict: Dict):
  bill_id = fileDict.get('bill_id')
  if bill_id:
    bill_id_parts = bill_id.split('-')
    return bill_id_parts[1] + bill_id_parts[0]
  else:
    logging.error('No bill_id: ' + str(fileDict.get('bill_type')))
    return None

def getCosponsors(fileDict: Dict, includeFields = []) -> list:
  """
  Gets Cosponsors from data.json Dict. `includeFields` is a list of keys to keep. The most useful are probably 'name' and 'bioguide_id'.

  Args:
      fileDict (Dict): the Dict created from data.json 
      includeFields (list): the fields in the cosponsor object to keep. If no 'includeFields' list is provided, all fields are preserved. 

  Returns:
      list: a list of cosponsors, with selected fields determined by includeFields 
  """
  cosponsors = fileDict.get('cosponsors')

  if includeFields:
    cosponsors = list(map(lambda cosponsor: { field: cosponsor.get(field) for field in includeFields }, cosponsors))

  return cosponsors 

def getBillTitles(fileDict: Dict, include_partial = True, billType = 'all') -> list:
  """
  Get a list of bill titles. If include_partial = True (default), gets all titles. Otherwise, gets only titles that correspond to the whole bill. 

  Args:
      fileDict (Dict): the Dict created from data.json 
      include_partial (bool, optional): Include titles for part of the bill. Defaults to True.
      billType (str, optional): Filter by billType (e.g. 'ih', 'rh', etc.) Defaults to 'all', which does not filter.

  Returns:
      list: a list of titles for the bill; either all titles or only whole-bill titles 
  """
  titles = fileDict.get('titles')
  if not include_partial:
    titles = [title for title in titles if not title.get('is_for_portion')]
  
  if (billType != 'all') and BILL_TYPES.get(billType):
    titles = [title for title in titles if BILL_TYPES.get(billType) == title.get('as')]
  return titles

def testWalkDirs():
  filePathList = []
  def addToFilePathList(dirName: str, fileName: str):
    filePathList.append(os.path.join(dirName, fileName))
    print('fpl: ' + str(filePathList))
  walkBillDirs(processFile=addToFilePathList)
  return filePathList

def loadBillsMeta(billMetaPath = PATH_TO_BILLS_META, zip = True):
  billsMeta = {}
  if zip:
    try:
      with gzip.open(billMetaPath + '.gz', 'rt', encoding='utf-8') as zipfile:
        billsMeta = json.load(zipfile)
    except:
      raise Exception('No file at' + billMetaPath + '.gz')
  else:
    try:
      with open(billMetaPath, 'r') as f:
        billsMeta = json.load(f)
    except:
      raise Exception('No file at' + billMetaPath + '.gz')
  
  return billsMeta

def saveBillsMeta(billsMeta: Dict, zip = True):
  with open(PATH_TO_BILLS_META, 'w') as f:
    json.dump(billsMeta, f)
    if zip:
      with gzip.open(PATH_TO_BILLS_META + '.gz', 'wt', encoding="utf-8") as zipfile:
        json.dump(billsMeta, zipfile)

def updateBillsMeta(billsMeta= {}, congress= ''):
  def addToBillsMeta(dirName: str, fileName: str):
    billDict = loadJSON(os.path.join(dirName, fileName))
    billCongressTypeNumber = getBillCongressTypeNumber(billDict)
    if not billCongressTypeNumber:
      return
    if not billsMeta.get(billCongressTypeNumber):
      billsMeta[billCongressTypeNumber] = {}
    titles = getBillTitles(billDict)
    billsMeta[billCongressTypeNumber]['titles'] = [title.get('title') for title in titles]
    billsMeta[billCongressTypeNumber]['titles_whole_bill'] = [title.get('title') for title in titles if not title.get('is_for_portion')]
    billsMeta[billCongressTypeNumber]['cosponsors'] = getCosponsors(fileDict=billDict, includeFields=['name', 'bioguide_id'])
    billCount = len(billsMeta.keys()) 
    if billCount % SAVE_ON_COUNT == 0:
      saveBillsMeta(billsMeta)

  walkBillDirs(processFile=addToBillsMeta)
  saveBillsMeta(billsMeta)
  return billsMeta



def main(args, loglevel):
  logging.basicConfig(format="%(levelname)s: %(message)s", level=loglevel)
  
  logging.info("You passed an argument.")
  logging.debug("Your Argument: %s" % args.argument)
  
  # TODO consider loading billsMeta before updating
  # TODO consider updating only current congress
  updateBillsMeta()
 
if __name__ == '__main__':
  parser = argparse.ArgumentParser( 
                                    description = "Generates billdata.json metadata file",
                                    epilog = "As an alternative to the commandline, params can be placed in a file, one per line, and specified on the commandline like '%(prog)s @params.conf'.",
                                    fromfile_prefix_chars = '@' )
  parser.add_argument(
                      "-a",
                      "--argument",
                      action='store',
                      dest='argument',
                      help="sample argument")
  parser.add_argument(
                      "-v",
                      "--verbose",
                      dest='verbose',
                      help="increase output verbosity",
                      action="store_true")
  args = parser.parse_args()
  
  # Setup logging
  if args.verbose:
    loglevel = logging.DEBUG
  else:
    loglevel = logging.INFO
  
  main(args, loglevel)