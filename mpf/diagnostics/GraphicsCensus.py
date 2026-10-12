"""Opt-in graphics ownership metadata; never binds or queries GL storage.

Qt attachment formats and array layouts provide estimates, not driver residency.
Worker-local allocation receipts are published as immutable scalar snapshots;
retired cohorts, host-only resources and driver overhead remain uncounted.
The startup ``toolhead/.profile-graphics`` marker enables this without GPU
timing queries; removing it stops standalone reports at the next interval.
"""
import time
from pathlib import Path


def snapshot(node, context):
    """Return plain values only, deduplicated in the correct GL namespaces."""
    rows, seen = [], set()
    group = id(context.shareGroup())
    local = id(context)

    def record(owner, kind, name, detail, *, shared=False, scope=None):
        name = int(name)
        key = (scope if scope is not None else group if shared else local, kind, name)
        if name <= 0 or key in seen: return
        seen.add(key)
        rows.append(dict(owner=owner, kind=kind, name=name, **detail))

    def mesh(label, value, ownership='owned mesh upload'):
        if value is None: return
        source = getattr(value, 'source', value)
        arrays = [getattr(source, name, None) for name in ('_vertices','_normals','_colors','_uvs')]
        arrays.extend(item.get('value') for item in getattr(value, '_attributes', {}).values())
        estimated = sum(int(getattr(array, 'nbytes', 0)) for array in arrays)
        if getattr(value, '_borrowed_native', None) is not None: ownership = 'borrowed native upload'
        for field, size in (('__vertex_buffer', estimated),
                            ('__index_buffer', int(getattr(getattr(source, '_indices', None), 'nbytes', 0)))):
            buffer = getattr(value, field, None)
            if buffer is not None:
                record(label+field, 'buffer', buffer.bufferId(),
                    dict(estimated_bytes=size, ownership=ownership, estimate='array layout; not queried allocation'), shared=True)

    visited = set()
    def geometry(label, value):
        if value is None or id(value) in visited: return
        visited.add(id(value))
        if getattr(value, '_context', context) not in (None, context): return
        mesh(label+'.upload', getattr(value, '_vertex_mesh', None))
        buffer = getattr(value, '_index_buffer', None)
        if buffer is not None:
            chunk = getattr(value, '_index_chunk', None)
            size = int(chunk)*4 if chunk else int(getattr(getattr(value, '_indices', None), 'nbytes', 0))
            record(label+'.indices', 'buffer', buffer.bufferId(), dict(estimated_bytes=size,
                ownership='owned', estimate='chunk upper bound' if chunk else 'full index layout'), shared=True)
        geometry(label+'.exterior', getattr(value, '_exterior', None))

    def target(label, value):
        if value is None: return
        try:
            size, fmt = value.size(), value.format()
            width, height, samples = size.width(), size.height(), fmt.samples()
            textures = tuple(value.textures()) if hasattr(value, 'textures') else (value.texture(),)
            colour_format = int(fmt.internalTextureFormat())
            attachment = int(getattr(fmt.attachment(), 'value', fmt.attachment()))
            colour_bytes = {0x8058: 4, 0x881A: 8, 0x8814: 16}.get(colour_format)
            # The sole plugin MRT layout is RGBA32F + two RGBA16F surfaces.
            if colour_format == 0x8814 and len(textures) == 3: colour_bytes = 32
            elif len(textures) > 1: colour_bytes = None
            estimate = (width*height*max(1, samples)*(colour_bytes+(4 if attachment else 0))
                        if colour_bytes is not None else None)
            estimate = getattr(value, 'allocation_bytes', estimate)
            record(label, 'fbo', value.handle(), dict(width=width, height=height,
                samples=samples, colour_format=colour_format, attachment=attachment,
                estimated_bytes=estimate,
                ownership='owned', estimate='format metadata; depth name and driver residency unknown'))
            for name in textures:
                record(label, 'texture', name, dict(ownership='owned', width=width, height=height), shared=True)
            # Explicit multisample targets retain raw colour/depth textures
            # in addition to their guarded Qt framebuffer.
            for name in getattr(value, '_names', ()):
                record(label, 'texture', name, dict(ownership='owned', width=width, height=height,
                    samples=4), shared=True)
            if hasattr(value, 'allocation_bytes'):
                target(label+'.qt-guard', getattr(value, '_fbo', None))
                target(label+'.read-staging', getattr(value, '_read_fbo', None))
        except Exception:
            rows.append(dict(owner=label, kind='unavailable', ownership='unknown'))

    def owner(label, value):
        if value is None: return
        # Never inspect wrappers from a retired or foreign rendering context.
        own_context = getattr(value, '_context', context)
        if own_context is not None and own_context is not context:
            rows.append(dict(owner=label, kind='foreign-context', ownership='unknown'))
            return
        for name in ('_fbo', '_resolved', '_work', '_resolve', '_result', '_front', '_merged',
                     '_sample_front', '_sample_merged'):
            target(label+'.'+name, getattr(value, name, None))
        for key, entry in getattr(value, '_pool', {}).items():
            for index, item in enumerate(entry):
                if hasattr(item, 'handle'): target(f'{label}.idle[{key}].{index}', item)

    for label, value in (('head', getattr(node, '_frame_cache', None)),
                         ('rotor', getattr(node, '_rotor_render', None))):
        owner(label, value)
    lighting = getattr(node, '_scene_lighting', None)
    surface = getattr(lighting, '_surface_cache', None)
    rows.append(dict(owner='lighting.coverage', kind='region',
        requested=getattr(lighting, '_composition_rect', None), retained=getattr(surface, '_region', None),
        output=getattr(lighting, '_output_region', None),
        view_size=getattr(surface, '_view_size', None), key_identity=id(getattr(surface, '_key', None))))
    get_mesh = getattr(node, 'getMeshData', None)
    if callable(get_mesh): mesh('head.opaque', get_mesh())
    mesh('head.translucent', getattr(node, '_translucent_mesh', None))
    for body, meshes in getattr(node, '_rotor_meshes', {}).items():
        for index, value in enumerate(meshes): mesh(f'head.rotor.{body}.{index}', value)
    for index, value in enumerate(getattr(node, '_static_transparent', ())): mesh(f'head.static-glass.{index}', value)
    for index, value in enumerate(getattr(lighting, '_plate_meshes', {}).values()): mesh(f'lighting.plate.{index}', value)
    for index, value in enumerate(getattr(lighting, '_path_geometry', {}).values()): geometry(f'lighting.path.{index}', value)
    for index, value in enumerate(getattr(getattr(node, '_environment_scene', None), '_geometry', {}).values()):
        geometry(f'reflection.main-path.{index}', value)
    for name in ('_frame_cache', '_surface_cache', '_depth_cache', '_prefix_depth_cache'):
        owner('lighting.'+name, getattr(lighting, name, None))
    foreground = getattr(getattr(node, '_occlusion', None), '_transparency', None)
    owner('foreground', foreground)
    simulation = getattr(node, '_simulation_pass', None)
    owner('simulation', simulation)
    owner('simulation.prefix', getattr(simulation, '_cache', None))
    geometry('simulation.path', getattr(simulation, '_geometry', None))
    shadow = getattr(simulation, '_instanced_shadow', None)
    storage = getattr(shadow, '_storage', None)
    if storage and getattr(shadow, '_context', None) is context:
        for name, size, ownership in (('vertex', storage['vertex_bytes'], 'borrowed'),
                                      ('lines', storage['count']*4, 'borrowed' if storage.get('index_owner') is not None else 'owned'),
                                      ('template', None, 'owned')):
            record('simulation.instanced.'+name, 'buffer', storage[name].bufferId(),
                dict(bytes=size, ownership=ownership), shared=True)
        for name in storage['textures']:
            record('simulation.instanced', 'texture', name,
                dict(ownership='owned view; no additional backing storage'), shared=True)
    environment = getattr(node, '_environment', None)
    binding = getattr(environment, '_storage', None)
    result = getattr(binding, 'result', None)
    worker = getattr(environment, '_worker', None)
    report = getattr(worker, 'graphics_census', None)
    if report is not None:
        serial, captured_at, worker_group, worker_context, entries = report
        rows.append(dict(owner='reflection.worker', kind='snapshot', serial=serial, captured_at=captured_at,
            stale=serial != getattr(result, 'serial', None), detail='last completed capture; retired cohorts excluded'))
        for label, kind, name, size, ownership in entries:
            record(label, kind, name, dict(bytes=size, ownership=ownership, capture_serial=serial),
                scope=worker_context if kind in ('fbo','vao','allocation-receipt') else worker_group)
    if result is not None:
        for name in ('colour', 'depth', 'hierarchy', 'full_hierarchy'):
            record('reflection.published.'+name, 'texture', getattr(result, name, 0),
                dict(ownership='worker-owned shared publication; bytes unknown'), shared=True)
        for index, entry in enumerate(getattr(getattr(result, 'frame', None), 'paths', ())):
            lease = entry[1]
            record('reflection.borrowed-path.'+str(index), 'buffer', lease.name,
                dict(bytes=lease.size, ownership='borrowed; frozen allocation receipt'), shared=True)
    if report is None:
        rows.append(dict(owner='reflection.worker', kind='incomplete',
            detail='No worker snapshot; worker-local targets, buffers and retired cohorts excluded.'))
    return rows


class GraphicsCensus:
    def __init__(self, marker_path=None, *, clock=time.monotonic, logger=None):
        self._clock, self._logger, self._next = clock, logger, 0.
        self._marker = Path(marker_path) if marker_path is not None else Path(__file__).resolve().parents[1] / 'toolhead' / '.profile-graphics'
        try: self._marker_permitted = self._marker.is_file()
        except OSError: self._marker_permitted = False

    def report(self, node, enabled, context):
        if not enabled and not self._marker_permitted: return
        try:
            now = self._clock()
            if now < self._next: return
            self._next = now+10.
            if not enabled and not self._marker.is_file(): return
            rows = snapshot(node, context)
            if self._logger is not None: self._logger(rows)
            else:
                from UM.Logger import Logger
                Logger.log('i', 'toolhead graphics census (metadata, not residency): %s', rows)
        except Exception:
            pass  # Diagnostics must never alter rendering or resource lifetime.


def worker_snapshot(scene, environment):
    """Called only by the capture thread; return immutable scalar receipts."""
    rows = []
    def add(label, kind, name, size=None, ownership='owned'):
        if name: rows.append((label, kind, int(name), None if size is None else int(size), ownership))
    for index, path in enumerate(scene.paths.values()):
        add(f'worker.path.{index}.index', 'buffer', path.index.name, path.index.size,
            'borrowed' if getattr(path.lease, 'index', None) is not None else 'owned')
        add(f'worker.path.{index}.vertex', 'buffer', path.lease.name, path.lease.size, 'borrowed')
        for vao in path.arrays.values(): add(f'worker.path.{index}', 'vao', vao.name)
    for index, buffers in enumerate(scene.plates.buffers.values()):
        for buffer in buffers: add(f'worker.plate.{index}', 'buffer', buffer.name, buffer.size)
    for vao in scene.plates.arrays.values(): add('worker.plate', 'vao', vao.name)
    if scene.texture is not None:
        image = scene.texture_key
        add('worker.platform', 'texture', scene.texture.name, image[0]*image[1]*4 if image else 4)
    storage = environment._storage
    if storage is not None:
        # Cube allocation order is colour pair, then optional depth pair.
        for index, name in enumerate(storage.names):
            size = (sum((512 >> level)**2 for level in range(10))*6*4 if index < 2 else 512*512*6*4)
            add('worker.cube', 'texture', name, size, 'owned; nominal full mip-chain estimate')
        face = storage.face
        for field, kind in (('framebuffer','fbo'),('texture','texture'),('renderbuffer','renderbuffer'),
                            ('ms_framebuffer','fbo'),('ms_colour','renderbuffer'),('ms_depth','renderbuffer')):
            add('worker.face.'+field, kind, getattr(face,field,0))
        add('worker.face.receipt', 'allocation-receipt', face.framebuffer, face.retained_bytes)
    return tuple(rows)


class WorkerGraphicsCensus(GraphicsCensus):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try: self._profile_enabled = self._marker.with_name('.profile-rendering').is_file()
        except OSError: self._profile_enabled = False

    def capture(self, serial, context, scene, environment):
        if not self._profile_enabled and not self._marker_permitted: return None
        try:
            now = self._clock()
            if now < self._next: return None
            self._next = now+10.
            if not self._profile_enabled and not self._marker.is_file(): return None
            return int(serial), float(now), id(context.shareGroup()), id(context), worker_snapshot(scene, environment)
        except Exception: return None
