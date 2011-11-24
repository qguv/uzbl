""" Basic cookie manager
    forwards cookies to all other instances connected to the event manager"""

from collections import defaultdict
import os, re

from uzbl.arguments import splitquoted
from uzbl.ext import GlobalPlugin, PerInstancePlugin

# these are symbolic names for the components of the cookie tuple
symbolic = {'domain': 0, 'path':1, 'name':2, 'value':3, 'scheme':4, 'expires':5}

# allows for partial cookies
# ? allow wildcard in key
def match(key, cookie):
    for k,c in zip(key,cookie):
        if k != c:
            return False
    return True

def match_list(_list, cookie):
    for matcher in _list:
        for component, match in matcher:
            if match(cookie[component]) is None:
                break
        else:
            return True
    return False

def add_cookie_matcher(_list, arg):
    ''' add a cookie matcher to a whitelist or a blacklist.
        a matcher is a list of (component, re) tuples that matches a cookie when the
        "component" part of the cookie matches the regular expression "re".
        "component" is one of the keys defined in the variable "symbolic" above,
        or the index of a component of a cookie tuple.
    '''

    args = splitquoted(arg)
    mlist = []
    for (component, regexp) in zip(args[0::2], args[1::2]):
        try:
            component = symbolic[component]
        except KeyError:
            component = int(component)
        assert component <= 5
        mlist.append((component, re.compile(regexp).search))
    _list.append(mlist)

class NullStore(object):
    def add_cookie(self, rawcookie, cookie):
        pass

    def delete_cookie(self, rkey, key):
        pass

class ListStore(list):
    def add_cookie(self, rawcookie, cookie):
        self.append(rawcookie)

    def delete_cookie(self, rkey, key):
        self[:] = [x for x in self if not match(key, splitquoted(x))]

class TextStore(object):
    def __init__(self, filename):
        self.filename = filename

    def as_event(self, cookie):
        """Convert cookie.txt row to uzbls cookie event format"""
        scheme = {
            'TRUE'  : 'https',
            'FALSE' : 'http'
        }
        if cookie[0].startswith("#HttpOnly_"):
            domain = cookie[0][len("#HttpOnly_"):]
        elif cookie[0].startswith('#'):
            return None
        else:
            domain = cookie[0]
        try:
            return (domain,
                cookie[2],
                cookie[5],
                cookie[6],
                scheme[cookie[3]],
                cookie[4])
        except (KeyError,IndexError):
            # Let malformed rows pass through like comments
            return None

    def as_file(self, cookie):
        """Convert cookie event to cookie.txt row"""
        secure = {
            'https' : 'TRUE',
            'http'  : 'FALSE'
        }
        return (cookie[0],
            'TRUE' if cookie[0].startswith('.') else 'FALSE',
            cookie[1],
            secure[cookie[4]],
            cookie[5],
            cookie[2],
            cookie[3])

    def add_cookie(self, rawcookie, cookie):
        assert len(cookie) == 6

        # delete equal cookies (ignoring expire time, value and secure flag)
        self.delete_cookie(None, cookie[:-3])

        first = not os.path.exists(self.filename)
        with open(self.filename, 'a') as f:
            if first:
                print >> f, "# HTTP Cookie File"
            print >> f, '\t'.join(self.as_file(cookie))

    def delete_cookie(self, rkey, key):
        if not os.path.exists(self.filename):
            return

        # read all cookies
        with open(self.filename, 'r') as f:
            cookies = f.readlines()

        # write those that don't match the cookie to delete
        with open(self.filename, 'w') as f:
            for l in cookies:
                c = self.as_event(l.split('\t'))
                if c is None or not match(key, c):
                    print >> f, l,

xdg_data_home = os.environ.get('XDG_DATA_HOME', os.path.join(os.environ['HOME'], '.local/share'))
DefaultStore = TextStore(os.path.join(xdg_data_home, 'uzbl/cookies.txt'))
SessionStore = TextStore(os.path.join(xdg_data_home, 'uzbl/session-cookies.txt'))

class Cookies(PerInstancePlugin):
    def __init__(self, uzbl):
        super(Cookies, self).__init__(uzbl)

        self.whitelist = []
        self.blacklist = []

        uzbl.connect('ADD_COOKIE', self.add_cookie)
        uzbl.connect('DELETE_COOKIE', self.delete_cookie)
        uzbl.connect('BLACKLIST_COOKIE', self.blacklist_cookie)
        uzbl.connect('WHITELIST_COOKIE', self.whitelist_cookie)

    # accept a cookie only when:
    # a. there is no whitelist and the cookie is in the blacklist
    # b. the cookie is in the whitelist and not in the blacklist
    def accept_cookie(self, cookie):
        if self.whitelist:
            if match_list(self.whitelist, cookie):
                return not match_list(self.blacklist, cookie)
            return False

        return not match_list(self.blacklist, cookie)

    def expires_with_session(self, cookie):
        return cookie[5] == ''

    def get_recipents(self):
        """ get a list of Uzbl instances to send the cookie too. """
        # This could be a lot more interesting
        return [u for u in self.uzbl.parent.uzbls.values() if u is not self.uzbl]

    def get_store(self, session=False):
        if session:
            return SessionStore
        return DefaultStore

    def add_cookie(self, cookie):
        cookie = splitquoted(cookie)
        if self.accept_cookie(cookie):
            for u in self.get_recipents():
                u.send('add_cookie %s' % cookie.raw())

            self.get_store(self.expires_with_session(cookie)).add_cookie(cookie.raw(), cookie)
        else:
            self.logger.debug('cookie %r is blacklisted', cookie)
            self.uzbl.send('delete_cookie %s' % cookie.raw())

    def delete_cookie(self, cookie):
        cookie = splitquoted(cookie)
        for u in self.get_recipents():
            u.send('delete_cookie %s' % cookie.raw())

        if len(cookie) == 6:
            self.get_store(self.expires_with_session(cookie)).delete_cookie(cookie.raw(), cookie)
        else:
            for store in set([self.get_store(session) for session in (True, False)]):
                store.delete_cookie(cookie.raw(), cookie)

    def blacklist_cookie(self, arg):
        add_cookie_matcher(self.blacklist, arg)

    def whitelist_cookie(self, arg):
        add_cookie_matcher(self.whitelist, arg)

# vi: set et ts=4: