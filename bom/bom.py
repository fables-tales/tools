# Routines for extracting BOMs from schematics
import subprocess, tempfile, os, sys, parts_db, schem
from decimal import Decimal
from threadpool import ThreadPool

PARTS_DB = os.path.expanduser("~/.sr/tools/bom/sr_component_lib")
if not os.path.exists( PARTS_DB ):
    print "Parts DB not found at \"%s\"" % PARTS_DB
    sys.exit(1)

STOCK_OUT = 0
STOCK_OK = 1
STOCK_UNKNOWN = 2

NUM_THREADS = 4

class PartGroup(list):
    """A set of parts
    One might call this a "BOM line" """
    def __init__(self, part, name = "", designators = [] ):
        list.__init__(self)

        for x in designators:
            self.append( (name, designators) )

        self.part = part
        self.name = name

    def stockcheck(self):
        """Check the distributor has enough parts in stock."""
        s = self.part.stockcheck()
        if s == None:
            return None

        if s < self.order_num():
            return False
        return True

    def order_num(self):
        """Returns the number of parts to order from the distributor.
        e.g. if we need 5002 components from a 5000 component reel, this
        will return 2."""

        if self.part.stockcheck() == None:
            "Unable to discover details from distributor..."
            # Assume one part per distributor unit
            return len(self)

        n = len(self)
        if n == 0:
            return 0

        # Change n to be in distributor units, rather than component units
        # (e.g. number of reels rather than number of components)
        d = n / self.part.get_dist_units()
        if n % self.part.get_dist_units() > 0:
            d = d + 1
        n = d

        if n < self.part.get_min_order():
            "Round up to minimum order"
            n = self.part.get_min_order()
        elif (n % self.part.get_increments()) != 0:
            n = n + (self.part.get_increments() - (n % self.part.get_increments()))

        # Some (hopefully) sane assertions
        assert n % self.part.get_increments() == 0
        assert n >= self.part.get_min_order()

        return n

    def get_price(self):
        """Returns the price"""
        n = self.order_num()

        p = self.part.get_price( n )
        if p == None:
            print "Warning: couldn't get price for %s (%s)" % (self.part["sr-code"], self.part["supplier"])
            return Decimal(0)

        return p * n

class Bom(dict):
    def stockcheck(self):
        """Check that all items in the schematic are in stock.
        Returns list of things that aren't in stock."""

        for pg in self.values():
            a = pg.stockcheck()

            if a == None:
                yield (STOCK_UNKNOWN, pg.part)
            elif not a:
                yield (STOCK_OUT, pg.part)
            else:
                yield (STOCK_OK, pg.part)

    def get_price(self):
        tot = Decimal(0)
        for pg in self.values():
            tot = tot + pg.get_price()
        return tot

class BoardBom(Bom):
    """BOM object.
    Groups parts with the same srcode into PartGroups.
    Dictionary keys are sr codes."""
    def __init__(self, db, fname, name ):
        """fname is the schematic to load from.  
        db is the parts database object.
        name is the name to give the schematic."""
        Bom.__init__(self)
        self.db = db
        self.name = name

        s = schem.open_schem(fname)

        for des,srcode in s.iteritems():
            if not self.has_key(srcode):
                self[srcode] = PartGroup( db[srcode], name )
            self[srcode].append((name,des))


class MultiBoardBom(Bom):
    def __init__(self, db):
        Bom.__init__(self)

        self.db = db

        # Array of 2-entry lists
        # 0: Number of boards
        # 1: Board
        self.boards = []

    def load_boards_args(self, args, allow_multipliers = True):
        mul = 1

        for arg in args:
           if arg[0] == '-' and allow_multipliers:
              mul = int(arg[1:])
           else:
              board = BoardBom( self.db, arg, os.path.basename( arg ) )
              self.add_boards(board, mul)

    def add_boards(self, board, num):
        """Add num boards to the collection.
        board must be a BoardBom instance."""

        # Already part of this collection?
        found = False
        for n in xrange(len(self.boards)):
            t = self.boards[n] 
            if t[1] == board:
                t[0] = t[0] + num
                found = True
                break

        if not found:
            self.boards.append( [num, board] )

        #### Update our PartGroup dictionary
        self.clear()
        
        for num, board in self.boards:
            
            # Mmmmm.  Horrible.
            for i in range(num):
                for srcode, bpg in board.iteritems():

                    if not self.has_key( srcode ):
                        self[srcode] = PartGroup( bpg.part )

                    self[srcode] += bpg

    def prime_cache(self):
        """Ensures that the webpage cache is filled in the
        quickest time possible by making many requests in
        parallel"""

	print "Getting data for parts from suppliers' websites"
        pool = ThreadPool(NUM_THREADS)

        for srcode, pg in self.iteritems():
            print srcode
            pool.add_task(pg.get_price)
        
        pool.wait_completion()

