"""The bots' society: their in-game identity (Profile) and their link to the game's social systems.

The profile lives in memory and is not written to a file (BotProfile.save is empty): the persistent record is the pack's state.json. update()
copies the bot's progress to the profile (level, money, honor, ship, drones, position); the directory and auction sections are the same dict
as the bot's save (persistent).

All of the game's "other player" lookups go through community.all_(): once the bots register there with a fake session (BotSession), the
directory, clan, group, whisper, mail, ranking and auction see them on their own. The bot's decisions are in tick() (the manager's thread):
  group invite       accept / decline (grouping is the norm: clan, the inviter's standing, company, current job), in the group follows the
                     leader (goals/group.py); bots look for a group with the same aim and invite players too (party, bot_invite_gap)
  friendship         accepts / declines an incoming request; sends a request to someone it played with or helped
  clan               a leader-minded, rich bot founds a clan; other bots apply to a clan that fits their personality; a bot leader
                     evaluates applications (the player's too); a bot with little loyalty may leave over time
  auction            traders and rich bots bid by value, raise in the last minutes
  chat               speech.py (Normal) or llm.py (Advanced)
"""
import os, random, re, time

import player_profile as PF
import paths as PTH
from . import name_style as NS
from . import catalog as K


class BotProfile(PF.Profile):
    def __init__(self, bot):
        self.bot = bot
        super().__init__({}, path=os.path.join(PTH.LOGS, 'bot_profili_yok.json'), d=_skeleton(bot))   # the path is never written

    def save(self, force=False):
        self.dirty = False


def _skeleton(bot):
    return {
        'version': 1, 'created': '2026-01-01 00:00:00',
        'pilot': {'userId': bot.uid, 'userName': bot.name, 'factionId': bot.company, 'clanId': 0, 'clanTag': '', 'level': 1, 'ep': 0,
                  'honourPoints': 0, 'credits': 0, 'uridium': 0, 'premium': False, 'dailyRank': 1, 'useSystemFont': False},
        'ship': {'typeId': 'ship_phoenix', 'speed': 320, 'shield': 0, 'shieldMax': 0, 'hitPoints': 104000, 'hitMax': 104000},
        'position': {'mapId': bot.game_map, 'x': 0, 'y': 0},
        'ships': {'1': {'lootId': 'ship_phoenix', 'hangarId': 1, 'configs': {}, 'active_config': 1}}, 'active_ship': 1,
        'drones': [], 'items': {}, 'ores': [], 'formation': 0, 'settings': {}, 'key_bindings': [], 'ui': {'featureBars': []},
        'contacts': bot.k.setdefault('contacts', {}), 'auction': bot.k.setdefault('auction', {'bids': {}}),
        'mailer': bot.k.setdefault('mailer', {}),
    }


def profile(bot):
    """The bot's profile (built on first request, updated on every request)."""
    if bot.profile is None:
        bot.profile = BotProfile(bot)
        import clan as KL
        me = KL.name(bot.profile)
        with KL.LOCK:
            k = next((c for c in KL.CLANS.values() if me in c['members']), None)
            if k is not None:
                KL._copy(k, bot.profile)          # the skeleton is built without a clan (clanId 0): a bot in the player's clan, after a restart,
                                                   # stayed clanless, did not obey orders and applied to another clan (live 2026-11-01)
    update(bot)
    return bot.profile


def update(bot):
    p = bot.profile
    if p is None:
        return
    k, d = bot.k, bot.value()
    pl, g = p.d['pilot'], p.d['ship']
    last = getattr(bot, '_fmt_money', None)
    if last is not None:                  # the game spent or gave from the profile (founding a clan, application fee, clan payment): passes to the bot
        k['credits'] = max(0, k['credits'] + int(pl.get('credits', 0)) - last[0])
        k['uridium'] = max(0, k['uridium'] + int(pl.get('uridium', 0)) - last[1])
    k['rank'] = rank(bot)                      # the panel's roster shows it too
    pl.update(userName=bot.name, factionId=bot.company, level=k['level'], ep=k['ep'], honourPoints=k['honor'], credits=k['credits'],
              uridium=k['uridium'], dailyRank=k['rank'])
    bot._fmt_money = (k['credits'], k['uridium'])
    ship = k['gear']['ship']
    p.d['ships']['1']['lootId'] = ship
    g.update(typeId=ship, speed=int(d['speed']), hitMax=int(d['hp']), shieldMax=int(d['shield']))
    if bot.body is None:
        g.update(hitPoints=int(d['hp']), shield=int(d['shield']))
    else:
        g.update(hitPoints=int(bot.body.hp), shield=int(bot.body.shield))
    p.d['position'].update(mapId=bot.game_map, x=int(k.get('x') or 0), y=int(k.get('y') or 0))
    p.d['drones'] = [dict(lootId=loot, level=1, uid=i) for i, (loot, n) in
                      enumerate((l, n) for l, n in k['gear'].get('drone', {}).items() for _ in range(n))]


def npc_rank_points(tip):
    """An alien's rank points (ranks.json npc_points, with npc_id; like the player's ranks.npc_points)."""
    import ranks as RT
    uid = K.NPC['types'].get(tip, {}).get('npc_id')
    return RT.R['npc_points'].get(str(uid), {}).get('points', 0)


def ship_rank_points(ship):
    """Rank points of a destroyed player ship (ranks.json ship_points; Goliath 20, Phoenix 1)."""
    import ranks as RT
    return RT.R['ship_points'].get(ship, {}).get('points', 0)


def points(bot):
    """Rank points, the same wiki formula as the player's (ranks.breakdown): EP/100,000 + honor/100 + player ship points x3 + level
    x100 + days since the save x6 + ship type x1,000 + alien points /2 + quest x100 - death x4 (player report 2026-10-31: bots that had
    played a long time stayed at a low rank; alien and player kills were not counted by number instead of type points, the ship type not by
    ship points, and days and deaths not at all)."""
    import ranks as RT
    F = RT.R['formula']
    k, i = bot.k, bot.k['stats']
    day = (bot.hour() - k['registered_at']) // 86400 if k.get('registered_at') else i.get('duration_sec', 0) // 86400   # old save: time played
    return k['ep'] * F['ep'] + k['honor'] * F['honor'] + k['level'] * F['level'] + i.get('npc_points', 0) * F['npc'] \
        + i.get('player_points', 0) * F['player'] + i.get('quest', 0) * F['quest'] + max(0, day) * F['day'] \
        + RT.R['ship_type'].get(k['gear']['ship'], 0) * F['ship'] + i.get('death', 0) * F['death_by_enemy']


def rank(bot):
    """The displayed rank icon (dailyRank 1-21): the position in the company (ranks.company_rank: Chief General 1 ...); by thresholds
    when there is no ranking, and as a ceiling with the Level Based Rank Threshold setting (ranks.capped)."""
    import ranks as RT
    p = points(bot)
    threshold = RT.R['thresholds']
    r = max(1, min(len(threshold), sum(1 for e in threshold if p >= e)))
    return RT.capped(r, RT.company_rank(bot.uid, bot.company, p))


# ------------------------------------------------------------------------------------------------------------ session and save
class BotSession:
    """An online bot's fake session (community.Person.session): the messages the game sends it fall into incoming()."""
    social = None
    shop = None
    world = None

    def __init__(self, bot):
        self.bot = bot
        self.profile = bot.profile

    def send(self, msgs, note=None):
        for m in msgs or ():
            incoming(self.bot, m)


def incoming(bot, m):
    """A message the game sent to the bot: invitations wait for a decision (tick)."""
    if m[0] == 'InvitationInitializationCommand' and m[1].get('candidateId') == bot.uid:
        bot.pending.append(('group', m[1]['inviterId'], m[1].get('inviterName', ''), time.time()))
    elif m[0] in ('GroupInitializationCommand', 'GroupMemberInitializationCommand'):
        bot.pending.append(('group_formed', 0, '', 0.0))   # (the bot invited, accepted): to the leader's side (tick)


_PEOPLE = {'t': 0.0, 'd': {}, 'y': None}


def people(y):
    """uid -> community.Person: all the pack's bots (profiles of offline ones too; mail, directory and clan know them). 2 s cache."""
    import community as TP
    now = time.time()
    if now - _PEOPLE['t'] < 2.0 and _PEOPLE['y'] is y:
        return _PEOPLE['d']
    out = {}
    for b in y.bots:
        if b.saved:                                    # a pilot that has not registered yet is not on the server
            out[b.uid] = person(b, TP)
    _PEOPLE.update(t=now, d=out, y=y)
    return out


def person(bot, TP=None):
    if TP is None:
        import community as TP
    p = profile(bot)
    if not bot.online:
        return TP.Person(p, None)
    if bot.session is None:
        bot.session = BotSession(bot)
    return TP.Person(p, bot.session)


def register(y, chat=None):
    """On the game server: bots connect to the community and chat."""
    import community as TP
    TP.BOTS = lambda: people(y)
    if chat is not None:
        chat.bot_hook = y.incoming_chat
        chat.bot_whisper_hook = y.incoming_whisper
        from . import moderation as MOD
        chat.block_hook = lambda user, path, lang: MOD.block(y, user, path, lang)
    y.chat = chat


def _refund(bot):
    """Money the game spent / gave from a bot's profile (founding a clan, application fee, clan payment) to the bot's save."""
    update(bot)


# ------------------------------------------------------------------------------------------------------------ decisions
def tick(y, now):
    """From the manager's tick (every 5 s): the online bots' social decisions. Each bot with its own interval (delayed like a human)."""
    for b in y.bots:
        if not b.online or b.dead_until > now:
            continue
        if b.pending:
            try:
                invites(y, b, now)
            except Exception as e:            # one invitation's error must not stop the other bots' social tick
                y.log('   bots: %s invitation error: %r' % (b.name, e))
                b.pending = []
        if now < getattr(b, 'next_social', 0):
            continue
        b.next_social = now + b.r.uniform(20, 70) * (1.5 - b.o('sociability'))
        profile(b)
        try:
            handshake(y, b, now)
            if y.permission('friend'):
                friendship(y, b, now)
            if y.permission('group'):
                party(y, b, now)
            if y.permission('clan'):
                clan_actions(y, b, now)
            if y.permission('auction'):
                auction(y, b, now)
            if y.permission('chat'):
                mail_tasks(y, b, now)
        except Exception as e:                # one bot's error must not stop the others
            y.log('   bots: %s social error: %r' % (b.name, e))


def handshake(y, b, now):
    """Hunting together with a grinder from the same company on the same map (shooting the same alien, not taking each other's box, brief
    banter): 'hunted_together' in both memories. Mostly someone it met before is chosen; the relationship turns into friendship, a group,
    over time. Before, relationships arose only from chat and help: in 44 minutes 423 of 499 pilots had met nobody."""
    from . import playstyle as PL
    if b.r.random() > 0.3 * (0.3 + b.o('sociability')) * min(1.3, PL.like(b, 'social')) or b.target is None \
            or b.target.name not in ('grinding', 'quest'):
        return
    nearby = [o for o in y.on_map.get(b.game_map, ()) if o is not b and o.company == b.company and o.online and o.dead_until <= now]
    if not nearby:
        return
    acquaintances = [o for o in nearby if b.memory.relation(o.name, now) > 0]
    o = b.r.choice(acquaintances) if acquaintances and b.r.random() < 0.6 else b.r.choice(nearby)
    b.memory.event(o.name, 'hunted_together', now)
    o.memory.event(b.name, 'hunted_together', now)


def invites(y, b, now):
    import group as GR
    remaining = []
    for kind, uid, name, t in b.pending:
        if now - t < b.k['personality']['reaction_sec'] * b.r.uniform(3, 9):
            remaining.append((kind, uid, name, t))
            continue
        if kind == 'group_formed':
            _group_up(y, b, now)
            continue
        if kind != 'group' or not y.permission('group'):
            continue
        k = person(b)
        if decide_group(y, b, name, uid, now):
            g = GR.group_of(b.uid)
            if g is not None and _human(uid) and not any(_human(u) for u in g.members):
                GR.handle(k, 'LeaveGroupRequest', {})        # a player's invitation beats a bot-only hunting party (decide_group weighed it)
                b.party_until = None
                b.event('left_group', now)
            GR.handle(k, 'InvitationAcceptRequest', {'inviterId': uid})
            b.memory.event(name, 'group', now)
            b.event('joined_group', now, name=name)
            from . import brain as B
            B.force(b, 'group', pay=0.8, duration=1800, now=now)
        else:
            GR.handle(k, 'InvitationRejectRequest', {'inviterId': uid})
            b.event('group_declined', now, name=name)
    b.pending = remaining


def _group_up(y, b, now):
    """The bot joined a group (its own invitation was accepted): goes to the group's human player (goals/group.py)."""
    from .goals import group as GG
    from . import brain as B
    k = GG.leader(b)
    if k is None or (b.target is not None and b.target.name == 'group'):
        return
    b.memory.event(k.name, 'group', now)
    b.event('joined_group', now, name=k.name)
    B.force(b, 'group', pay=0.8, duration=1800, now=now)


PARTY_MIN = (20 * 60, 100 * 60)          # how long a bot-only group hunts together before someone leaves
PARTY_SIZE = (2, 5)                      # a bot leader fills its bot-only party up to this many (random per party)
PAIR_GAP = 3 * 3600                      # a bot does not invite the same player again for this long (a refusal or an ignored invite)
BUSY = ('gate', 'mode', 'pvp_match', 'duel')      # jobs a pilot does not drop for a group
HUMAN_INVITE = {}                        # player uid -> last bot invitation sent to them (bot_invite_gap)
ALLIANCE, NAP, WAR = 1, 2, 3             # clan.clan_relation


def _human(uid):
    return uid < 1800000000 or uid >= 1900000000         # bot uids are 18xxxxxxxx (state.py)


def _zone(m):
    """What a map means for grouping across companies: 'coop' 4-5 and 5-X (the Uber and pirate aliens are too strong at first: better
    to group with the enemy than to die), 'battle' 4-1..4-4 (the PvP maps: only allied clans), 'company' the others."""
    import map_links as H
    name = str(H.name(int(m or 0)))
    return 'coop' if name == '4-5' or name.startswith('5-') else 'battle' if name.startswith('4-') else 'company'


def _clan_id(k):
    import clan as KL
    c = KL.clan_of(k.profile) if k is not None and k.profile is not None else None
    return c['id'] if c else None


def _map_of(k):
    return int(k.profile.d['position'].get('mapId', 0) or 0) if k is not None and k.profile is not None else 0


def _bot_of(y, uid):
    return next((o for o in y.bots if o.uid == uid), None)


def _power_of(y, k):
    """A pilot's combat power as the others judge it (catalog.power_score): a bot's equipment, a player's ship in the game; None if
    unknown (a player not in a map)."""
    o = _bot_of(y, k.uid) if k is not None and not _human(k.uid) else None
    if o is not None:
        return o.value()['power']
    w = getattr(k, 'world', None) if k is not None else None
    if w is not None:
        from . import ship as G
        return G.hero_power(w)
    return None


def _standing(b, power):
    """How much the bot gains (> 0) or loses (< 0) by grouping with a pilot of this power: a stronger one speeds its progress, a much
    weaker one slows it, less so for a helpful pilot who likes carrying newcomers (trait helpfulness)."""
    import math
    mine = max(1.0, b.value()['power'])
    if not power:
        return 0.0
    r = math.log2(max(0.05, power / mine))
    if r >= 0:
        return min(0.15, 0.1 * r)
    return max(-0.4, 0.2 * r * (1.25 - b.o('helpfulness')))     # helpfulness 1: a quarter of the penalty


def _appeal(b, power):
    """How much the bot wants to invite a pilot of this power (party's choice): a stronger one is sought after, the more the stronger
    (a top player gets invitations), a weaker one only by a helpful pilot."""
    import math
    if not power:
        return 0.0
    r = max(-2.0, min(2.0, math.log2(max(0.05, power / max(1.0, b.value()['power'])))))
    return 0.35 * r if r >= 0 else 0.3 * r * (1.25 - b.o('helpfulness'))


def _openness(b):
    """Willingness to cooperate with another company (0-1): low for an aggressive, competitive pilot (who shoots on sight instead)."""
    return max(0.0, min(1.0, 1.2 - 0.6 * b.o('aggression') - 0.6 * b.o('rivalry')))


def _diplo(b, company, clan, m):
    """The diplomatic part of grouping with a pilot of this company and clan on map m (decide_group's terms): same clan > allied clan >
    same company; another company: possible on 'coop' maps, almost never on the PvP maps unless the clans are allied, rare elsewhere;
    clans at war: no."""
    import clan as KL
    mine = b.k.get('clan')
    if mine is not None and clan == mine:
        return 0.3
    rel = KL.clan_relation(mine, clan) if mine is not None and clan is not None else 0
    if rel == WAR:
        return -1.5
    if company == b.company:
        return 0.15 if rel == ALLIANCE else 0.0
    z = _zone(m)
    if rel == ALLIANCE:
        return -0.1 if z != 'company' else -0.3
    if z == 'coop':
        return -0.3 - 0.4 * (1 - _openness(b))
    if z == 'battle':
        return -1.5
    return -0.75 - 0.2 * (1 - _openness(b))


def party(y, b, now):
    """Bots look for a group like real players: grouping shares the rewards and the quest kills, so it is the norm. A free pilot (or the
    leader of a bot-only party with room) invites someone with the same aim (same map, same job), friendly (company, clan, alliance,
    friends) and as strong or stronger (standing; a helpful pilot also takes weaker ones): players near it, in its clan or friends too, a
    strong player more often. Another company only where diplomacy allows (4-5 and 5-X; allied clans). The invited one decides with
    decide_group. A bot-only party breaks up after a while. Invitations to a player: at most one every bot_invite_gap seconds per player
    (0: none), never twice from the same bot within PAIR_GAP. Before, only a friend or acquaintance of the same company was invited,
    about once in four hours per bot, and never a player (player request 2026-11-22)."""
    import group as GR
    import community as TP
    from . import playstyle as PL
    g = GR.group_of(b.uid)
    if g is not None:
        humans = [u for u in g.members if _human(u)]
        until = b.__dict__.get('party_until')
        if not humans and until is not None and now > until:
            GR.handle(person(b), 'LeaveGroupRequest', {})
            b.party_until = None
            b.party_cd = now + b.r.uniform(1800, 2 * 3600)     # does not form a new group right away
            b.event('left_group', now)
            return
        size = b.__dict__.setdefault('party_size', b.r.randint(*PARTY_SIZE))
        if humans or g.leader != b.uid or len(g.members) >= size or len(g.members) >= GR.MAX_COUNT:
            return                                       # only the leader of a bot-only party with room keeps recruiting
    if now < b.__dict__.get('party_cd', 0):
        return
    if b.target is None or b.target.name not in ('grinding', 'quest', 'resting', 'exploring', 'pvp', 'group'):
        return
    if b.r.random() > 0.06 * (0.4 + b.o('sociability')) * min(1.5, PL.like(b, 'social')) * (0.3 if PL.has(b, 'lone_wolf') else 1.0):
        return
    my_map = int(b.game_map or 0)
    friends = set(b.k.get('friends') or [])
    gap = y.setting('bot_invite_gap', 90)
    asked = b.__dict__.setdefault('asked', {})
    cands = []
    for k in TP.all_().values():                         # players: those near, clanmates and friends anywhere
        if not _human(k.uid) or k.session is None or k.profile is None or GR.group_of(k.uid) is not None:
            continue
        if not gap or now - HUMAN_INVITE.get(k.uid, 0) < gap or now - asked.get(k.uid, 0) < PAIR_GAP:
            continue
        same_map, clan, friend = _map_of(k) == my_map, _clan_id(k), k.name in friends
        if not (same_map or friend or (clan is not None and clan == b.k.get('clan'))):
            continue
        d = _diplo(b, int(k.profile.d['pilot'].get('factionId', 0) or 0), clan, my_map)
        if d <= -1.0:
            continue
        score = 0.5 * same_map + 0.4 * friend + d + _appeal(b, _power_of(y, k)) + b.memory.relation(k.name, now)
        cands.append((score, k.uid, k.name, None))
    for o in y.bots:                                     # bots: the same aim
        if o is b or not o.online or o.dead_until > now or GR.group_of(o.uid) is not None:
            continue
        same_map, clan, friend = o.game_map == b.game_map, o.k.get('clan'), o.name in friends
        if not (same_map or friend or (clan is not None and clan == b.k.get('clan'))):
            continue
        d = _diplo(b, o.company, clan, my_map)
        if d <= -1.0 or (d < -0.2 and b.r.random() > 0.3):    # across companies a pilot rarely asks first
            continue
        same_job = o.target is not None and b.target is not None and o.target.name == b.target.name
        score = 0.5 * same_map + 0.3 * same_job + 0.4 * friend + d + _appeal(b, o.value()['power']) + b.memory.relation(o.name, now)
        cands.append((score, o.uid, o.name, o))
    if not cands:
        return
    score, uid, name, o = max(cands, key=lambda x: x[0] + b.r.uniform(0, 0.3))
    if TP.find(uid=uid) is None:
        return
    GR.handle(person(b), 'GroupInvitationRequest', {'candidateId': uid})
    if o is None:
        HUMAN_INVITE[uid] = asked[uid] = now
    if g is None:
        b.party_until = now + b.r.uniform(*PARTY_MIN)
        b.party_size = b.r.randint(*PARTY_SIZE)
    if o is not None:
        o.party_until = (b.__dict__.get('party_until') or now + PARTY_MIN[0]) + b.r.uniform(-600, 600)
    b.event('group_invite', now, name=name)


def decide_group(y, b, name, uid, now):
    """Does it accept the group invitation. Grouping is the norm (shared rewards and quest kills): most pilots accept. What counts: the
    inviter's power (a stronger one speeds progress, a much weaker one slows it, less so for a helpful pilot: standing), diplomacy
    (same clan > allied clan > same company; another company on 4-5 / 5-X if the pilot is open to it, almost never on 4-1..4-4 unless
    the clans are allied, rare elsewhere; clans at war: no), friendship and memory; a lone wolf, fatigue or a job it would not drop
    (gate, mode, duel) lower it. Already in a bot-only party: a player's invitation can still win (invites leaves the party). If it said
    "inv me" in chat (promise) it accepts. Before, about half accepted, a clanmate had no bonus and every level of gap counted against
    the inviter, so the strongest player was refused most (5% for a level 25 player)."""
    from . import interaction as IX
    if IX.has_promise(b, 'group', name, now):
        return True
    import community as TP
    import group as GR
    from . import playstyle as PL
    o = TP.find(uid=uid)
    p = 0.72 + 0.2 * b.o('sociability') + 0.4 * b.memory.relation(name, now) - 0.25 * b.mood['fatigue']
    p *= min(1.15, PL.like(b, 'social') ** 0.5) * (0.6 if PL.has(b, 'lone_wolf') else 1.0)      # a lone wolf rarely groups (playstyle.py)
    if b.target is not None and b.target.name in BUSY:
        p -= 0.35
    elif b.target is not None and b.target.name == 'quest':
        p -= 0.05                                        # quest kills are shared in a group: hardly a reason to refuse
    if o is not None and o.profile is not None:
        p += _standing(b, _power_of(y, o))
        p += _diplo(b, int(o.profile.d['pilot'].get('factionId', 0) or 0), _clan_id(o), _map_of(o))
        if name in (b.k.get('friends') or []):
            p += 0.1
    g = GR.group_of(b.uid)
    if g is not None and _human(uid) and not any(_human(u) for u in g.members):
        p -= 0.15                                        # leaves its bot party for him: a little less likely
    return b.r.random() < max(0.01, min(0.98, p))


def friendship(y, b, now):
    import contacts as CT
    r = CT.save(b.profile)
    k = None
    for name in list(r.get('incoming', [])):
        k = k or person(b)
        rel = b.memory.relation(name, now)
        from . import playstyle as PL
        accept = b.r.random() < (0.2 + 0.5 * b.o('sociability') + rel) * min(1.2, PL.like(b, 'social')) ** 1.5 \
            and not (PL.has(b, 'lone_wolf') and rel < 0.5)                         # a lone wolf says no to strangers
        CT.handle(k, 'ContactRequest', {'contactRequestType': CT.ACCEPT if accept else CT.REJECT, 'userName': name})
        b.event('friend_accept' if accept else 'friend_refuse', now, name=name)
        if accept:
            b.memory.event(name, 'friend', now)
    from . import playstyle as PL
    if b.r.random() < 0.15 * b.o('sociability') * PL.like(b, 'social') ** 2:
        friendly = [a for a in b.memory.allies(0.25, now) if not CT._var(r.get('friend', []), a) and not CT._var(r.get('outgoing', []), a)]
        if friendly:
            name = b.r.choice(friendly)
            CT.handle(k or person(b), 'ContactRequest', {'contactRequestType': CT.CREATE, 'userName': name})
            b.event('friend_request', now, name=name)


# ------------------------------------------------------------------------------------------------------------ clan
_PREFIX = ('Dark', 'Black', 'Red', 'Iron', 'Silent', 'Eternal', 'Royal', 'Shadow', 'Storm', 'Crimson', 'Galactic', 'Void', 'Star')
_PREFIX_TR = ('Kara', 'Kizil', 'Anadolu', 'Gok', 'Demir', 'Olumsuz', 'Turk', 'Yildiz')
_SUFFIX = ('Legion', 'Knights', 'Wolves', 'Eagles', 'Raiders', 'Hunters', 'Order', 'Empire', 'Brotherhood', 'Alliance', 'Reapers',
         'Guardians', 'Titans', 'Pirates')
_SUFFIX_TR = ('Kurtlar', 'Kartallar', 'Akincilar', 'Savascilar', 'Lejyon', 'Ordu', 'Sahinler', 'Korsanlar')


_ULKE = {'tr': 'Turkish', 'de': 'German', 'pl': 'Polish', 'ru': 'Russian', 'es': 'Spanish', 'fr': 'French', 'pt': 'Brazilian'}
_SINGLE = ('Bandidos', 'Nocturnos', 'Inferno', 'Phantoms', 'Outlaws', 'Spartans', 'Vikings', 'Immortals', 'Mercenaries', 'Renegades',
        'Elite', 'Legends', 'Nemesis', 'Valhalla', 'Ragnarok', 'Apocalypse', 'Chaos', 'Exodus', 'Oblivion', 'Hellfire')
_TAIL = ('Power', 'Army', 'Force', 'Elite', 'Warriors', 'Family', 'Team', 'Mafia')


def clan_name(b, r, tags=()):
    """Clan name and tag as on the real servers: two-part name, one word (sometimes fancy, lowercase or wrapped), "The X",
    "Turkish Power"; the tag first from the pack's real tag pool (DOStats), otherwise initials, sometimes fancy (Λ И Σ)."""
    lang = b.k['personality'].get('name_lang') or b.k['personality'].get('lang')
    tr = lang == 'tr' and r.random() < 0.7
    style = r.choices(('two_word', 'single', 'the', 'country', 'lowercase', 'wrapped'), (35, 20, 10, 15 if lang in _ULKE else 0, 10, 10))[0]
    single = r.choice(_SINGLE + (_SUFFIX_TR if tr else _SUFFIX))
    if style == 'two_word':
        name = '%s %s' % (r.choice(_PREFIX_TR if tr else _PREFIX), r.choice(_SUFFIX_TR if tr else _SUFFIX))
    elif style == 'single':
        name = NS._swap_letters(single.upper(), r) if r.random() < 0.5 else single
    elif style == 'the':
        name = 'The ' + single
    elif style == 'country':
        name = '%s %s' % (_ULKE[lang], r.choice(_TAIL))
    elif style == 'lowercase':
        name = single.lower() + r.choice(('', '', '.', ' clan', ' squad'))
    else:
        on, last = NS._sec(r, NS.WRAPS[:8])
        name = on + single + last
    if tags and r.random() < 0.7:
        return name, r.choice(list(tags))[:4]
    plain = re.sub(r'[^A-Za-z ]', '', NS.plain(name) if not name.isascii() else name).split() or [single]
    tag = (''.join(w[0] for w in plain).upper() if len(plain) > 1 else plain[0][:r.choice((3, 3, 4))].upper())
    tag += r.choice(('', '', str(r.randint(1, 99)), 'X', 'TR' if tr else 'EU')) if len(tag) < 3 else ''
    if r.random() < 0.3:
        tag = ''.join(r.choice(NS.UPPER_LOOKALIKES[c]) if c in NS.UPPER_LOOKALIKES and r.random() < 0.6 else c for c in tag)
    return name, tag[:4]


def clan_actions(y, b, now):
    import clan as KL
    p = profile(b)
    k = KL.clan_of(p)
    if k is None:
        b.k['clan'] = None
        leader_like = b.archetype == 'leader' or b.o('sociability') * b.o('ambition') > 0.5
        bot_clans = sum(1 for c in KL.CLANS.values() if c['leader'] in y.name_index)
        limit = max(1, sum(b.saved for b in y.bots) // int(y.setting('bots_per_clan', 12) or 12))
        if (y.permission('clan_found') and leader_like and b.level >= 5 and bot_clans < limit
                and b.k['credits'] >= KL.FOUNDING_COST * 1.3 and b.r.random() < 0.3):
            r = random.Random('%s|klan|%d' % (b.name, len(KL.CLANS)))
            kl = KL.Clan(p, log=y.log)
            for _ in range(6):
                name, tag = clan_name(b, r, y.package.get('clan_tags') or ())
                if KL.Clan.valid_tag(tag) and not kl.taken(name, tag):      # separated tags like [CM] do not occur
                    break
            description = 'PvP' if b.o('aggression') > 0.6 else 'PvE & quests'
            out = kl.handle('FoundClanRequest', {'clanTag': tag, 'clanName': name, 'clanDescription': description})
            _refund(b)
            if out and out[0][1].get('success'):
                b.k['clan'] = out[0][1]['clanId']
                b.event('clan_founded', now, name=name, tag=tag)
                b.mood.it(morale=0.2)
            return
        if b.level >= 3 and b.r.random() < 0.25 * (0.4 + b.o('sociability')):
            candidate = pick_clan(y, b, list(KL.CLANS.values()))
            if candidate is not None and b.k['credits'] >= KL.APPLICATION_FEE * 3:
                msg = application_text(b, candidate, now)
                KL.Clan(p, log=y.log).handle('ApplyClanRequest', {'clanId': candidate['id'], 'message': msg})
                _refund(b)
                b.event('applied_to_clan', now, clan=candidate['name'])
        return
    if b.k.get('clan') != k['id']:
        b.k['clan'] = k['id']
        b.event('joined_clan', now, clan=k['name'])
    pay_tax(b, k)
    kl = KL.Clan(p, log=y.log)
    if kl.right('hiremember', k):
        for bs in list(k.get('applications', [])):
            if not application_decision(y, b, k, bs, now):
                continue
            accept = application_decision(y, b, k, bs, now, final=True)
            kl.handle('ApplyAcceptRequest' if accept else 'ApplyDeclineRequest', {'applyId': bs['id']})
            b.event('application_accept' if accept else 'application_refuse', now, name=bs.get('name'))
            if accept and str(bs.get('name', '')).lower() not in y.name_index:
                welcome_mail(y, b, k, bs.get('name', ''))
    if y.permission('clan_diplomacy') and kl.right('diplo_start', k):
        diplomacy(y, b, k, kl, now)
    from . import clan_management as CMG
    CMG.manage(y, b, k, kl, now)                           # ranks, description, announcement, tax, budget, payment, members (clan_management)
    if KL.clan_of(p) is not k:
        return                                           # resigned the leadership or disbanded the clan
    if k['leader'] != b.name.lower() and b.o('loyalty') < 0.3 and b.r.random() < 0.004:
        kl.handle('LeaveClanRequest', {})
        b.k['clan'] = None
        b.event('left_clan', now, clan=k['name'])


def clan_relation(a, b):
    """Relationship between two bots' clans (1 alliance, 2 non-aggression, 3 war, 0 none)."""
    import clan as KL
    return KL.clan_relation(int(a.k.get('clan') or 0), int(b.k.get('clan') or 0))


def pay_tax(b, k):
    """The clan's tax from a member's last earnings into the treasury (deducted from the alien reward in the game; clan.tax)."""
    import clan as KL
    income = b.__dict__.pop('_clan_income', 0)
    pay = income * int(k.get('tax') or 0) // 100
    if pay <= 0:
        return
    b.k['credits'] = max(0, b.k['credits'] - pay)
    with KL.LOCK:
        k['treasury'] += pay
        KL.add_income(k, pay)
        u = k['members'].get(b.name.lower())
        if u is not None:
            u['contrib_period'] = int(u.get('contrib_period', 0)) + pay     # to the biggest contributors at the weekly payout (clan_management)


def clan_company(c):
    """The clan's company; for a clan open to all companies (0), the leader's (like the ordering in clan.py)."""
    return int(c.get('company') or 0) or int((c.get('members', {}).get(c.get('leader')) or {}).get('company') or 0)


def _members(c):
    return {str(a).lower() for a in c.get('members', {})}


def diplomacy(y, b, k, kl, now):
    """A clan leader's diplomacy, with the game's clan diplomacy (alliance 1, non-aggression 2, war 3, peace request 0): replies to incoming
    requests within a few minutes (including your clan's), and sends requests itself from time to time. Alliance or non-aggression with
    clans from the same company; war on a clan from another company that killed our members or is an equal match (500,000 credits from the
    treasury; if there is none the leader deposits it); peace in a prolonged war. Personality: sociability likes alliance, aggression
    likes war, caution likes peace."""
    import clan as KL
    incoming = [r for r in k.get('diplomacy_requests', []) if r['to'] == k['id']]
    if incoming and b.r.random() < 0.35:
        r = incoming[0]
        o = KL.CLANS.get(r['from_clan'])
        if o is not None:
            enemy = len(_members(o) & {a.lower() for a in b.memory.grudges(-0.3, now)})
            il = _clan_relation(y, b, o, now)[0]
            if r['type'] == 0:                       # peace
                accept = b.r.random() < 0.25 + 0.5 * (1 - b.o('aggression')) - 0.1 * enemy
            else:
                # it can be from another company too, but if it is the player's clan and it does not think badly of them: non-aggression
                # right away, a good relationship for an alliance
                same = (clan_company(o) == clan_company(k)
                        or bool(_humans(y, o)) and il >= (0.2 if r['type'] == 1 else 0.0))
                accept = same and not enemy and b.r.random() < 0.3 + 0.5 * b.o('sociability') + (0.2 if r['type'] == 2 else 0) + 0.4 * max(0, il)
            kl.handle('ClanEditDiploOfferRequest', {'diploId': r['id'], 'status': 'allow' if accept else 'deny'})
            b.event('diplomacy', now, form=r['type'], clan=o['name'], tag=o.get('tag', ''), accept=accept, incoming=True)
        return
    # a new initiative is rare (the leader thinks about it roughly once an hour); twice as often if it has a reason (help, grudge) with
    # the player's clan
    has_reason = any(_clan_relation(y, b, c, now)[2] for c in list(KL.CLANS.values()) if c['id'] != k['id'] and _humans(y, c))
    if b.r.random() > (0.03 if has_reason else 0.015):
        return
    existing = {d['clan']: d for d in k.get('diplomacy', [])}
    last = b.k.setdefault('last_diplo', {})            # does not repeat an offer to a clan: one that was rejected or left unanswered rests for 6 hours
    pending = {r['to'] for r in k.get('diplomacy_requests', [])} | {r['from_clan'] for r in k.get('diplomacy_requests', [])}
    wars = [d for d in existing.values() if d['type'] == 3]
    for d in wars:                              # prolonged war: asks for peace
        if d['clan'] not in pending and b.r.random() < 0.15 * (1.2 - b.o('aggression')):
            kl.handle('ClanNewDiplomacyRequest', {'diploClanID': d['clan'], 'diploForm': 0, 'desc': 'gg, peace?'})
            b.event('diplomacy', now, form=0, clan=d.get('name', ''), incoming=False)
            return
    company = clan_company(k) or b.company
    enemies = {a.lower() for a in b.memory.grudges(-0.3, now)}
    candidates = []
    for c in list(KL.CLANS.values()):
        if c['id'] == k['id'] or c['id'] in existing or c['id'] in pending or not c.get('members'):
            continue
        if now - last.get(str(c['id']), 0) < 6 * 3600:
            continue
        cc = clan_company(c)
        if _humans(y, c):                         # player clan: size is not considered (a one-person clan also gets an offer), the relationship decides
            il, grudge, reason = _clan_relation(y, b, c, now)
            if (grudge or il <= -0.3) and cc != company and cc and b.o('aggression') > 0.4 and len(wars) < 2:
                candidates.append((0.8 + 0.3 * b.o('aggression') + 0.25 * min(3, grudge), 3, c))        # killed our members
            elif il >= 0.2 and (cc == company or il >= 0.25):
                candidates.append((0.7 + 0.5 * il + b.r.uniform(0, 0.2), 1 if b.o('sociability') > 0.5 or il >= 0.5 else 2, c))   # helped
            elif cc == company and il > -0.3:
                candidates.append((0.4 * b.o('sociability') + 0.4 + b.r.uniform(0, 0.3), 1 if b.o('sociability') > 0.6 else 2, c))
            continue
        size = 1.0 - min(1.0, abs(len(c['members']) - len(k['members'])) / max(5.0, len(k['members'])))
        if cc == company:
            kind = 1 if b.o('sociability') > 0.5 else 2
            candidates.append((0.4 * b.o('sociability') + 0.3 * size + b.r.uniform(0, 0.3), kind, c))
        elif cc and b.o('aggression') > 0.55 and len(wars) < 2:
            grudge = len(_members(c) & enemies)
            candidates.append((0.4 * b.o('aggression') + 0.3 * size + 0.25 * min(3, grudge) + b.r.uniform(0, 0.3), 3, c))
    if not candidates:
        return
    points, kind, c = max(candidates, key=lambda a: a[0])
    if points < 0.55:
        return
    if kind == 3 and k['treasury'] < KL.WAR_COST:
        missing = KL.WAR_COST - k['treasury']
        if b.k['credits'] < missing * 1.5:
            return                                  # cannot afford to risk war
        b.earn(credits=-missing)                       # the leader deposits into the treasury
        with KL.LOCK:
            k['treasury'] += missing
        KL.changed(k)
    text = {1: 'alliance?', 2: 'nap?', 3: 'see you in space'}[kind]
    out = kl.handle('ClanNewDiplomacyRequest', {'diploClanID': c['id'], 'diploForm': kind, 'desc': text})
    if out and out[0][1].get('success'):
        last[str(c['id'])] = now
        b.event('diplomacy', now, form=kind, clan=c['name'], tag=c.get('tag', ''), incoming=False)


def _humans(y, c):
    """The real players in the clan (members who are not bots)."""
    return _members(c) - set(y.name_index)


def _clan_relation(y, b, c, now):
    """The bot's relationship to a clan (with its members in memory): (best/worst relationship, number of grudge-holding members, is there
    a reason). A player's clan that helped or killed ours counts as having a "reason" for diplomacy."""
    name = _members(c)
    relations = [b.memory.relation(a, now) for a in name if a in b.memory.d]
    grudge = len(name & {a.lower() for a in b.memory.grudges(-0.3, now)})
    il = max(relations) if relations and max(relations) >= 0.2 else min(relations) if relations and min(relations) <= -0.3 else 0.0
    return il, grudge, abs(il) >= 0.2 or bool(grudge)


def pick_clan(y, b, clans):
    """The clan that best fits the bot's personality: same company, close to its members' average level, PvP/PvE description, member count
    (a crowd attracts but it is not full). A bot with a pending or rejected application does not apply again."""
    candidates = []
    for c in clans:
        if any(str(x.get('name', '')).lower() == b.name.lower() for x in c.get('applications', [])):
            return None
        if c.get('company') and c['company'] != b.company or b.name.lower() in [str(r).lower() for r in c.get('rejected', [])]:
            continue
        uy = list(c['members'].values())
        if len(uy) >= 50:
            continue
        avg = sum(int(u.get('level', 1)) for u in uy) / max(1, len(uy))
        pvp = 'pvp' in str(c.get('description', '')).lower()
        affinity = 1.0 - abs(avg - b.level) / 12.0 + (0.3 if pvp == (b.o('aggression') > 0.6) else 0) + min(0.3, len(uy) / 30.0)
        affinity += 2.0 * _friend_member(b, c)[1]             # the clan of someone it helped, grouped with or befriended stands out
        candidates.append((affinity + b.r.uniform(-0.2, 0.2), c))
    return max(candidates, key=lambda x: x[0])[1] if candidates else None


def _friend_member(b, c):
    """The clan member the bot has the closest relationship with: (name, relationship 0..1); if there is no good (>= 0.2) relationship
    (None, 0)."""
    en = (None, 0.0)
    for a, e in b.memory.d.items():
        if a in c.get('members', {}) and b.memory.relation(a) >= max(0.2, en[1]):
            en = (e.get('name', a), b.memory.relation(a))
    return en


def application_text(b, c, now=None):
    """The text of the clan application (in the bot's language, from patterns): if it knows a member, the reason (they helped or grouped),
    otherwise level and play style."""
    from . import speech as SPC
    name, il = _friend_member(b, c)
    lv, style = b.level, ('pvp' if b.o('aggression') > 0.6 else 'pve')
    f = dict(name=name or '', clan=c.get('name', ''), lv=lv, style=style)
    if name:
        key = 'clan_apply_friend' if b.memory.count(name, 'helped_me') or b.memory.count(name, 'friend') else 'clan_apply_group'
    else:
        key = 'clan_apply_pvp' if style == 'pvp' else 'clan_apply_pve'
    return SPC.template(b, key, **f) or 'lvl %d, %s' % (lv, style)


def application_decision(y, b, k, bs, now, final=False):
    """Does the bot leader look at the application (kesin=False: has the reaction time elapsed) / accept it (kesin=True): level,
    relationship. The player's application is also evaluated this way (not at once, after some time)."""
    if not final:
        t = bs.get('_bot_seen')
        if t is None:
            bs['_bot_seen'] = now
            return False
        return now - t > b.r.uniform(30, 240)
    from . import interaction as IX
    if IX.has_promise(b, 'clan', bs.get('name', ''), now):
        return True                                      # said "apply, I'll accept" in chat
    import community as TP
    o = TP.find(name=bs.get('name'))
    lv = int(o.profile.d['pilot'].get('level', 1)) if o is not None else 1
    uy = list(k['members'].values())
    avg = sum(int(u.get('level', 1)) for u in uy) / max(1, len(uy))
    p = 0.55 + 0.05 * (lv - avg + 3) + 0.6 * b.memory.relation(bs.get('name', ''), now)
    return b.r.random() < max(0.1, min(0.97, p))


# ------------------------------------------------------------------------------------------------------------ auction
def lot_importance(b, lot):
    """The lot's importance to the bot: 0 does not want it; 1 ordinary (ammo, booster, resource; a trader wants every lot); 2 equipment
    (laser, generator, drone); 3 unequipped CPU or module; 4 a ship better than the current one; 5 a ship it is saving up for. The
    auction's real value is CPUs and big ships: in the official game they are bought with credits instead of saving uridium for days, so
    the competition is for them (user request 2026-10-30)."""
    from . import extras as EX
    loot = lot['loot']
    if loot.startswith('ship_'):
        g, current = K.ITEMS['ships'].get(loot) or {}, K.ITEMS['ships'].get(b.k['gear']['ship']) or {}
        # only those with values and better than the current one (designs that are only appearance, "goliath_design_jade", are not ships)
        if not g.get('hp') or g['hp'] + 20000 * (g['laser'] + g['generators']) <= current.get('hp', 0) + 20000 * (
                current.get('laser', 0) + current.get('generators', 0)):
            return 0
        return 5 if b.k.get('savings') == loot else 4
    if lot.get('group') in ('extra', 'module'):
        return 0 if EX.var(b, loot) else 3
    if loot in K.ITEMS['lasers'] or loot in K.ITEMS['shield_generators'] or loot in K.ITEMS['drones'] or lot.get('group') == 'generator':
        return 2
    if loot.startswith('ammunition_') or lot.get('group') in ('booster', 'special') or b.archetype == 'trader':
        return 1
    return 0


def auction(y, b, now):
    """Bid: defends the bid it was outbid on every social round; otherwise looks at lots now and then, picks an important one (CPU, a good
    ship). The ceiling is the lot's buy-now value x importance, competition, ambition and closeness to the end of the period; a share of
    free credits by ambition. settle_bot gives the results (the item if it wins, credits back if outbid)."""
    import auction as MZ
    settle_bot(b, now)
    from . import playstyle as PL
    browse = bool(PL.state(b).get('auction')) and b.r.random() <= 0.15      # only in some sessions, many players hardly ever (playstyle.interest)
    if not browse and not b.k.get('auction', {}).get('bids'):
        return                                         # an outbid bid is still defended on every round (not only when browsing)
    from . import loot as LT
    free = b.k['credits'] * (1 - PL.reserve(b)['C']) - b.clan_savings()
    if free < 3 * MZ.D.get('min_bid', 10000):
        return
    candidates = []
    for lot in list(MZ.LOTS.values()):
        importance = lot_importance(b, lot)
        if importance:
            end = MZ.period_end(lot['type'], now)
            own = b.k['auction']['bids'].get('%s|%s' % (lot['type'], lot['loot']))
            candidates.append((importance, lot, end, int(own['bid']) if own and own.get('end_time') == end else 0))
    defend = [a for a in candidates if a[3] and a[3] <= (MZ.best(MZ.bids(a[1], a[2], skip=b.profile)) or (0,))[0]]
    if defend:
        importance, lot, end, old = max(defend, key=lambda a: a[0])           # outbid: counter (returns to the window like a player)
    else:
        request = (0.6 if b.archetype == 'trader' else 0.2) * (0.5 + b.o('rivalry')) * (0.6 + 0.8 * b.o('ambition'))
        if not browse or not candidates or b.r.random() > request:
            return
        weight = [a[0] ** 3 * (1 + (a[1].get('price') or 0) / 50000.0) for a in candidates]      # important and valuable lot first
        importance, lot, end, old = b.r.choices(candidates, weights=weight)[0]
        if old:
            return                                   # already in the lead
    loot = lot['loot']
    key = '%s|%s' % (lot['type'], loot)
    # the lot's price is the uridium of the buy-now (for all of it): converted to credits (it used to be taken as credits, cheap lots got
    # no bids)
    value = int((lot.get('price') or 0) * LT.EXCHANGE_RATE) or 1000000
    remaining = max(0.0, (end - now) / MZ.SPAN[lot['type']])
    ambition = b.o('ambition')
    cap = value * (0.35 + 0.4 * b.o('rivalry') + 0.25 * (1 - remaining)) * (0.7 + 0.3 * (b.archetype == 'trader'))
    if importance >= 3:
        cap *= 1 + ambition + 0.25 * (importance - 3)       # CPU and ship: an ambitious bot goes up to twice the buy-now value
    cap = min(cap, free * (0.5 + (0.45 * ambition if importance >= 3 else 0)))
    rival = MZ.best(MZ.bids(lot, end, skip=b.profile))
    current_time = rival[0] if rival else 0
    bid = max(MZ.D.get('min_bid', 10000), int(current_time * b.r.uniform(1.05, 1.25)) + MZ.D.get('increase', 1000))
    if not rival:                                  # nobody bid yet: a real auction is contested, the opening is a market price (bots used
        bid = max(bid, int(value * b.r.uniform(0.3, 0.55)))   # to win ships for the 10,000 minimum bid at level 3)
    if bid > cap and current_time + MZ.D.get('increase', 1000) <= cap:
        bid = int(cap)                          # down to its last penny: the highest without exceeding the ceiling
    if bid > cap or not b.pay('C', bid - old):
        return
    b.k['auction']['bids'][key] = {'bid': bid, 'end_time': end, 'time': int(now)}
    b.event('auction_bid', now, item=loot, bid=bid)


def welcome_mail(y, b, k, name):
    from . import agent_tools as TL
    from . import speech as SPC
    text = SPC.template(b, 'mail_welcome', clan=k['name'], tag=k['tag'])
    if text:
        TL.call(y, b, 'send_mail', {'name': name, 'subject': '[%s] %s' % (k['tag'], k['name']), 'text': text})


def mail_tasks(y, b, now):
    """Incoming mail: a human's (not the pack's bots) unread message is read after a while; the request in it (group, clan, friendship,
    come) is applied by the decision engine and replied to by mail (in Advanced mode the language model writes the text)."""
    import mail as PS
    p = profile(b)
    with p.lock:
        incoming = [r for r in p.d.get('mailer', {}).get('incoming', ()) if r.get('state') == PS.UNREAD]
    for r in incoming:
        if r.get('type') not in (PS.USER, PS.CLAN) or str(r.get('sender', '')).lower() in y.name_index or not r.get('sender'):
            with p.lock:
                r['state'] = PS.READ                       # system, auction, bot mail: read and passed
            continue
        seen = r.setdefault('_bot_seen', now)
        if now - seen < b.k['personality']['reaction_sec'] * 20 + b.r.uniform(30, 300):
            continue                                         # looks at mail not at once, but after a few minutes
        with p.lock:
            r['state'] = PS.READ
        reply_mail(y, b, r, now)
        return                                               # one mail per tick


def mail_template(b, k):
    """The pattern text of the decision suited to mail (chat patterns are too short for mail: "inv me", "no thx") or None."""
    from . import speech as SPC
    if not k:
        return None
    if k['intent'] == 'group':
        return SPC.template(b, 'mail_group_yes' if k['accept'] else 'mail_group_no')
    text = SPC.template(b, k['template'][0], **k['template'][1]) if k.get('template') else None
    return text and (text + ', ' + SPC.template(b, 'mail_reply') if len(text.split()) < 4 else text)


def reply_mail(y, b, r, now):
    from . import agent_tools as TL
    from . import interaction as IX
    from . import speech as SPC
    import community as TP
    name = r['sender']
    o = TP.find(name=name)
    who = {'userId': name, 'id': str(r.get('sender_id', 0)),
           'factionId': str(o.profile.d['pilot'].get('factionId', 0)) if o is not None else '0'}
    text = '%s\n%s' % (r.get('subject', ''), r.get('text', ''))
    intent, game_map = SPC.intent(text)
    k = IX.decision(y, b, intent, name, who, game_map, text, now, mailer=True) if intent in IX.INTENTS and intent != 'assist' else None
    IX.apply(y, b, k)
    b.memory.event(name, 'chat', now)
    b.event('mail_received', now, name=name)
    subject = 'Re: ' + str(r.get('subject', ''))[:46]
    if y.llm is not None and y.llm.ready and y.llm.mailer(y, b, r, k):
        return                                               # Advanced: the model writes the text (llm._mail)
    body = mail_template(b, k) or SPC.template(b, 'mail_reply')
    if body and (k is not None or b.r.random() < 0.85):           # if it made a decision it does not stay silent
        TL.call(y, b, 'send_mail', {'name': name, 'subject': subject, 'text': body + ('' if len(body) >= 5 else ' :)')})


def _outbid_unseen(b, lot, bid):
    """A real auction has bidders the bot never sees: the closer the bid is to the lot's value, the likelier it wins (a 30% bid wins
    about one time in five). Before, bots were the only bidders and won almost every lot they touched."""
    import auction as MZ
    from . import loot as LT
    value = int((lot.get('price') or 0) * LT.EXCHANGE_RATE) or 1000000
    p = max(0.08, min(0.95, (bid / value - 0.15) / 0.75))
    if b.r.random() < p:
        return False
    b.event('auction_lost', item=lot.get('loot'))
    return True


def settle_bot(b, now):
    """Bot bids in finished periods: the item if it is the highest, otherwise the credits back."""
    import auction as MZ
    t = b.k['auction']['bids']
    for key, bid in list(t.items()):
        if bid['end_time'] + 120 > now:
            continue                                     # a bit later so the player's result (Muzayede.settle) sees the bot's bid
        kind, loot = key.split('|', 1)
        lot = MZ.LOTS.get((kind, loot))
        en = MZ.best(MZ.bids(lot, bid['end_time'])) if lot else None
        del t[key]
        if lot and en and en[3] == b.uid and not _outbid_unseen(b, lot, int(bid['bid'])):
            g = b.k['auction'].setdefault('history', [])          # the player's auction history shows it too (Muzayede.history)
            g.insert(0, {'group': lot['group'], 'who': b.uid, 'bid': int(bid['bid']), 'date': MZ.stamp(bid['end_time'] - MZ.SPAN[kind]),
                         'loot': loot, 'type': kind, 'name': b.name})
            del g[MZ.D['history_limit']:]
            if not b.add(loot, lot.get('count', 1)):
                b.earn(credits=int(bid['bid']) // 2)       # useless to it: sells (half value)
            b.event('auction_won', now, item=loot)
            b.mood.it(morale=0.15)
        else:
            b.earn(credits=int(bid['bid']))
