"""
Quality profiles and sizes for the Sonarr / Radarr settings tabs.

A profile lists qualities lowest first — some grouped, e.g. "WEB 1080p" is
WEBRip-1080p + WEBDL-1080p — each allowed or not, plus the cutoff the app
upgrades up to. Sizes (MB per minute of runtime) belong to each quality,
not to a profile, so changing one applies to every profile that allows it.
Quality ids and group ids share one id space (groups start at 1000), which
is also what the cutoff refers to.
"""
from concurrent.futures import ThreadPoolExecutor

from .clients import ArrError
from .services import client


def overview(kind):
    c = client(kind)
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_profiles = pool.submit(c.quality_profiles)
        f_definitions = pool.submit(c.quality_definitions)
        f_limits = pool.submit(c.quality_definition_limits)
        profiles, definitions, limits = f_profiles.result(), f_definitions.result(), f_limits.result()
    return {
        'profiles': [_profile(p) for p in profiles],
        'definitions': {str(d['quality']['id']): _definition(d) for d in definitions},
        'limits': {'min': limits.get('min', 0), 'max': limits.get('max', 1000)},
    }


def _profile(p):
    items = []
    for it in reversed(p.get('items') or []):  # best first, as Sonarr/Radarr show them
        if it.get('quality'):
            q = it['quality']
            items.append({'id': q['id'], 'name': q['name'], 'allowed': bool(it.get('allowed')), 'group': False,
                          'qualities': [{'id': q['id'], 'name': q['name']}]})
        else:
            members = [x['quality'] for x in reversed(it.get('items') or [])]
            items.append({'id': it['id'], 'name': it.get('name') or '', 'allowed': bool(it.get('allowed')), 'group': True,
                          'qualities': [{'id': m['id'], 'name': m['name']} for m in members]})
    return {'id': p['id'], 'name': p['name'], 'upgrade_allowed': bool(p.get('upgradeAllowed')),
            'cutoff': p.get('cutoff'), 'items': items}


def _definition(d):
    return {'id': d['id'], 'title': d.get('title') or d['quality']['name'],
            'min': d.get('minSize'), 'preferred': d.get('preferredSize'), 'max': d.get('maxSize')}


def save_profile(kind, profile_id, upgrade_allowed, cutoff, allowed):
    """allowed = {item id: bool} for the qualities/groups being switched on or off."""
    c = client(kind)
    p = c.quality_profile(profile_id)
    allowed_ids = set()
    for it in p['items']:
        item_id = str(it['quality']['id'] if it.get('quality') else it['id'])
        if item_id in allowed:
            it['allowed'] = bool(allowed[item_id])
        if it['allowed']:
            allowed_ids.add(int(item_id))
    if not allowed_ids:
        raise ArrError('Allow at least one quality')
    cutoff = int(cutoff) if str(cutoff or '').isdigit() else p.get('cutoff')
    if cutoff not in allowed_ids:
        raise ArrError('"Upgrade until" has to be one of the allowed qualities')
    p['upgradeAllowed'] = bool(upgrade_allowed)
    p['cutoff'] = cutoff
    return c.update_quality_profile(p)


def _size(value):
    """'' / None → no limit; otherwise a non-negative number."""
    if value in (None, ''):
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        raise ArrError(f'"{value}" isn\'t a number')


def save_definitions(kind, changes):
    """changes = [{quality_id, min, preferred, max}] in MB per minute."""
    c = client(kind)
    current = {str(d['quality']['id']): d for d in c.quality_definitions()}
    updated = []
    for ch in changes:
        d = current.get(str(ch.get('quality_id')))
        if not d:
            continue
        low, preferred, high = _size(ch.get('min')) or 0, _size(ch.get('preferred')), _size(ch.get('max'))
        if high is not None and (low > high or (preferred is not None and preferred > high)):
            raise ArrError(f'{d.get("title")}: the maximum has to be at least the minimum and preferred size')
        if preferred is not None and preferred < low:
            raise ArrError(f'{d.get("title")}: the preferred size can\'t be below the minimum')
        d.update(minSize=low, preferredSize=preferred, maxSize=high)
        updated.append(d)
    if updated:
        c.update_quality_definitions(updated)
    return len(updated)
