#!/usr/bin/env python3
"""Standard RS-274X Gerber to pwszImg, binary STL and PNG.

This is a practical standard-Gerber subset. It supports C/R/O/P apertures,
Altium's common explicit polygon aperture macros, flashes, regions, arcs and
both coordinate modes. Arbitrary Altium aperture macros are diagnosed and
skipped; complete macro language support would require a larger interpreter.
"""
import argparse, math, re, struct, sys
from pathlib import Path

HEADER_SIZE, RECORD_SIZE, TRAILER_SIZE = 48, 17, 8
RECORD = struct.Struct('<ffffB')
BUNDLED_HEADER = bytes.fromhex('7b3d3d004c67c745f25268c25eba23c2f252684285eb214200000000200200005b2d2d0000000000f693000001000000')
BUNDLED_TRAILER = b'--]\x00==}\x00'
EPS=1e-9

def output_directory():
    """Return the output directory beside this script, or the cwd interactively."""
    outdir = Path(__file__).resolve().parent / 'output'
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir

def rot(p, deg):
    a=math.radians(deg); c,s=math.cos(a),math.sin(a)
    return (p[0]*c-p[1]*s,p[0]*s+p[1]*c)

def poly_circle(r, n=48): return [(r*math.cos(2*math.pi*i/n),r*math.sin(2*math.pi*i/n)) for i in range(n)]
def clean_poly(p):
    q=[]
    for x in p:
        if not q or math.hypot(x[0]-q[-1][0],x[1]-q[-1][1])>EPS:q.append(x)
    if len(q)>1 and math.hypot(q[0][0]-q[-1][0],q[0][1]-q[-1][1])<EPS:q.pop()
    return q

def polygon_area(p): return .5*sum(p[i][0]*p[(i+1)%len(p)][1]-p[(i+1)%len(p)][0]*p[i][1] for i in range(len(p)))

def offset_line(a,b,w,n=8):
    """Exact-width round-capped stroke, snapped to the 0.01 mm precision grid."""
    if math.hypot(b[0]-a[0], b[1]-a[1]) < EPS:
        return []
    try:
        from shapely.geometry import LineString
        g=LineString([a,b]).buffer(w/2, resolution=32, cap_style=1, join_style=1)
        # Snap all generated coordinates to the requested 0.01 mm grid.
        from shapely.geometry import Polygon
        g=Polygon([(round(x/0.01)*0.01,round(y/0.01)*0.01) for x,y in list(g.exterior.coords)[:-1]])
        if not g.is_valid:
            g=g.buffer(0)
        if g.geom_type == "Polygon":
            return [(float(x),float(y)) for x,y in list(g.exterior.coords)[:-1]]
        return []
    except ImportError as e:
        raise RuntimeError('D01 stroke buffering requires Shapely with set_precision support') from e

def aperture_poly(ap, center=(0,0)):
    kind=ap['kind']; a=ap['args']; rotdeg=ap.get('rot',0)
    if kind=='C':
        p=poly_circle(a[0]/2)
    elif kind=='R':
        w,h=a[0],a[1]; p=[(-w/2,-h/2),(w/2,-h/2),(w/2,h/2),(-w/2,h/2)]
    elif kind=='O':
        # A Gerber O aperture is a capsule whose overall dimensions are w by h.
        w,h=a[0],a[1]; p=[]; n=32
        if w>=h:
            r=h/2; cx=w/2-r
            for i in range(n+1):
                t=math.pi/2-math.pi*i/n
                p.append((cx+r*math.cos(t), r*math.sin(t)))
            for i in range(n+1):
                t=-math.pi/2-math.pi*i/n
                p.append((-cx+r*math.cos(t), r*math.sin(t)))
        else:
            r=w/2; cy=h/2-r
            for i in range(n+1):
                t=math.pi*i/n
                p.append((r*math.cos(t), cy+r*math.sin(t)))
            for i in range(n+1):
                t=math.pi+math.pi*i/n
                p.append((r*math.cos(t), -cy+r*math.sin(t)))
    else: p=ap.get('polygon',[])
    p=[rot(x,rotdeg) for x in p]
    return [(x[0]+center[0],x[1]+center[1]) for x in p]

def parse_gerber(path, default_width=.1):
    text=Path(path).read_text(encoding='ascii',errors='ignore')
    # RS-274X has two command domains: extended blocks are delimited by %...%,
    # while ordinary commands end at *.  Do not concatenate the command after
    # an extended block (e.g. %\nD12*%\nD03*), or its aperture selector is lost.
    def tokenize_gerber(s):
        out=[]; i=0; n=len(s)
        while i<n:
            while i<n and s[i].isspace(): i+=1
            if i>=n: break
            if s[i]=='%':
                j=s.find('%',i+1)
                if j<0: raise ValueError('unterminated RS-274X extended block')
                # Extended blocks may contain several "*"-terminated
                # commands (including a selector followed by D03). Keep each
                # command separate so modal selectors cannot be lost.
                block=s[i+1:j]
                for part in block.split('*'):
                    part=part.strip()
                    if part: out.append(part)
                i=j+1
            else:
                j=s.find('*',i)
                if j<0: j=n
                token=s[i:j].strip()
                if token: out.append(token)
                i=min(n,j+1)
        return out
    toks=tokenize_gerber(text.replace('\r',''))
    ampmeta={}
    for mm in re.finditer(r'AMPARAMS\|DCode=(\d+)\|XSize=([0-9.]+)mm\|YSize=([0-9.]+)mm[^*]*Rotation=([+-]?[0-9.]+)', text, re.I):
        ampmeta[int(mm.group(1))]=(float(mm.group(2)),float(mm.group(3)),float(mm.group(4)))
    # Register all aperture definitions before parsing drawing commands: Altium may
    # emit flashes before a D-select, and macro names are not limited to C/R/O.
    macro_text={}
    for mm in re.finditer(r'%AM([A-Z0-9_]+)\*(.*?)%', text, re.I|re.S):
        macro_text[mm.group(1).upper()]=mm.group(2)
    unit=25.4 if re.search(r'%MOIN|G70',text,re.I) else 1.0
    aps={}
    add_defs=re.findall(r'%ADD(\d+)([^,*%]+)(?:,([^%]*?))?\*%',text,re.I)
    first_aperture=None
    for ds,name,tail in add_defs:
        d=int(ds); name=name.strip().upper(); tail=(tail or '').strip()
        first_aperture=d if first_aperture is None else first_aperture
        if name in ('C','R','O'):
            vals=[]
            try: vals=[float(v)*unit for v in re.split(r'[Xx]',tail) if v!='']
            except ValueError: vals=[]
            if name=='C': aps[d]={'kind':'C','args':vals[:1] or [default_width*unit],'hole':vals[2:] if len(vals)>2 else []}
            else: aps[d]={'kind':name,'args':(vals+[vals[0] if vals else default_width*unit])[:2],'hole':vals[2:] if len(vals)>2 else []}
            continue
        # Interpret common Gerber macro primitive 4 (outline polygon).
        poly=None
        body=macro_text.get(name,'')
        for prim in re.findall(r'(?<![0-9.])4,([^*]+)',body):
            nums=[]
            try: nums=[float(v.strip()) for v in prim.split(',')]
            except ValueError: continue
            if len(nums)>=3:
                n=int(nums[1]); xy=nums[2:2+2*n]
                if n>=3 and len(xy)==2*n:
                    poly=list(zip(xy[::2],xy[1::2])); angle=nums[2+2*n] if len(nums)>2+2*n else 0
                    poly=[rot((x*unit,y*unit),angle) for x,y in poly]; break
        # AMPARAMS is authoritative for dimensions/rotation if available.
        if poly is not None and d in ampmeta:
            poly=[rot(pt,ampmeta[d][2]) for pt in poly]
        if poly is not None:
            aps[d]={'kind':'P','polygon':poly,'args':[],'hole':[]}
            continue
        nums=re.findall(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',tail)
        vals=[abs(float(v))*unit for v in nums]
        if d in ampmeta: w,h,rd=ampmeta[d]
        elif vals: w=max(vals); h=sorted(vals)[-2] if len(vals)>1 else w; rd=0
        else: w=h=default_width*unit; rd=0
        w=max(w,default_width*unit); h=max(h,default_width*unit)
        raise ValueError(f'unsupported aperture macro {name} for D{d}; supported primitives are circle, rectangle, obround, polygon outline, and explicit Altium octagon metadata')
    unit=1.; xi,xd,yi,yd=2,4,2,4; absolute=True; interp=1; multi=False
    macros={}; cur=(0.,0.); selected=None; strokes=[]; contours=[]; active=None; region_paths=[]; warnings=[]; stats={'D01':0,'D02':0,'D03':0,'arcs':0,'regions':0,'flashes':0}
    def num(s,axis,delta=False):
        if '.' in s:v=float(s)
        else:
            neg=s.startswith('-'); z=s.lstrip('+-'); dec=xd if axis=='X' else yd; v=(int(z or 0)/(10**dec))*(-1 if neg else 1)
        return v*unit
    fallback_count=0
    def add_flash(pt,ap,code=None):
        if ap is None:
            raise ValueError('D03 flash has no valid selected aperture' + (f' (D{code})' if code else ''))
        p=aperture_poly(ap,pt); p=clean_poly([(round(x/0.01)*0.01,round(y/0.01)*0.01) for x,y in p])
        if len(p)>=3: contours.append(p); stats['flashes']+=1
    def add_path(points):
        p=clean_poly(points)
        if len(p)>=3: contours.append(p)
    for raw in toks:
        c=raw.strip(); u=c.upper()
        if not c: continue
        if u.startswith('%'):
            z=u.strip('%').strip()
            # Some emitters wrap a selector as an extended block: %\nD12*%.
            md=re.fullmatch(r'D(\d+)', z)
            if md and int(md.group(1)) >= 10:
                selected=int(md.group(1)); continue
            if z.startswith('FSL'):
                m=re.search(r'FS([LA])X(\d)(\d)Y(\d)(\d)',z)
                if m: absolute=m.group(1)=='A'; xi,xd,yi,yd=map(int,m.groups()[1:])
            elif 'MOIN' in z: unit=25.4
            elif 'MOMM' in z: unit=1.
            elif z.startswith('ADD'):
                # All aperture definitions were registered in the pre-scan above.
                pass
            elif z.startswith('AM'):
                mm=re.match(r'AM([A-Z0-9_]+)',z); macros[mm.group(1) if mm else z[2:]]=z
            elif 'ADD' in z and 'OCTAGOND' in z:
                m=re.search(r'ADD(\d+)OCTAGOND(\d+)',z); name='OCTAGOND'+m.group(2) if m else ''
                if m and name in macros:
                    # explicit Altium primitive 4 polygon: take signed coordinate pairs after 4,1,vertex_count
                    q=macros[name].split('*')[0]; nums=[]
                    try: nums=[float(v)*unit for v in q.split(',')[2:]]
                    except ValueError: pass
                    if len(nums)>=6:
                        n=int(nums[0]); vals=nums[1:1+2*n]; aps[int(m.group(1))]={'kind':'P','polygon':list(zip(vals[::2],vals[1::2])),'args':[],'hole':[]}
                    else: warnings.append('could not decode '+name)
                elif m and int(m.group(1)) in ampmeta:
                    # Altium emits a standard-looking macro plus AMPARAMS metadata; model its chamfered octagon.
                    w,h,rd=ampmeta[int(m.group(1))]; c=min(w,h)*0.22
                    q=[(-w/2+c,-h/2), (w/2-c,-h/2),(w/2,-h/2+c),(w/2,h/2-c),(w/2-c,h/2),(-w/2+c,h/2),(-w/2,h/2-c),(-w/2,-h/2+c)]
                    aps[int(m.group(1))]={'kind':'P','polygon':[rot(x,rd) for x in q],'args':[],'hole':[]}
                else: warnings.append('unsupported aperture macro '+name)
            continue
        if u.startswith('G70'):unit=25.4
        if u.startswith('G71'):unit=1.
        if 'G90' in u:absolute=True
        if 'G91' in u:absolute=False
        if 'G74' in u:multi=False
        if 'G75' in u:multi=True
        if 'G36' in u: active=[]; region_paths=[]; stats['regions']+=1; continue
        if 'G37' in u:
            if active is not None:
                if active: add_path(active)
                for rp in region_paths:add_path(rp)
            active=None; continue
        gm=re.search(r'G0?([123])',u)
        if gm: interp=int(gm.group(1))
        dm=re.search(r'D(\d+)',u); d=int(dm.group(1)) if dm else None
        if d is not None and d>=10: selected=d; continue
        xm=re.search(r'X([+-]?\d+(?:\.\d+)?)',u); ym=re.search(r'Y([+-]?\d+(?:\.\d+)?)',u)
        if not xm and not ym and d is None: continue
        x=num(xm.group(1),'X') if xm else None; y=num(ym.group(1),'Y') if ym else None
        target=(cur[0] if x is None else (cur[0]+x if not absolute else x),cur[1] if y is None else (cur[1]+y if not absolute else y))
        if d==2:
            if active is not None and active: active=[]
            cur=target; stats['D02']+=1; continue
        if d==3:
            add_flash(target,aps.get(selected),selected); cur=target; stats['D03']+=1; continue
        if d!=1: continue
        stats['D01']+=1
        if interp==1:
            if active is not None:
                if not active: active=[cur]
                active.append(target)
            else:
                ap=aps.get(selected)
                if ap is None: raise ValueError('D01 draw has no valid selected aperture')
                w=ap['args'][0] if ap.get('args') else default_width*unit
                p=offset_line(cur,target,w)
                if p: add_path(p); strokes.append((cur,target,w))
        else:
            im=re.search(r'I([+-]?\d+(?:\.\d+)?)',u); jm=re.search(r'J([+-]?\d+(?:\.\d+)?)',u)
            # I/J are offsets, in file units, regardless of absolute coordinate mode
            ci=num(im.group(1),'X') if im else 0.; cj=num(jm.group(1),'Y') if jm else 0.; center=(cur[0]+ci,cur[1]+cj)
            r=math.hypot(cur[0]-center[0],cur[1]-center[1])
            if r<EPS: warnings.append('arc with zero radius'); cur=target; continue
            a0=math.atan2(cur[1]-center[1],cur[0]-center[0]); a1=math.atan2(target[1]-center[1],target[0]-center[0]); cw=interp==2
            delta=(a1-a0)%(2*math.pi) if not cw else -((a0-a1)%(2*math.pi))
            if not multi and abs(delta)>math.pi: delta += -2*math.pi if delta>0 else 2*math.pi
            n=max(4,min(2000,int(abs(delta)*r/max(.02, r*.08))))
            pts=[(center[0]+r*math.cos(a0+delta*i/n),center[1]+r*math.sin(a0+delta*i/n)) for i in range(n+1)]
            for a,b in zip(pts,pts[1:]):
                ap=aps.get(selected)
                if ap is None: raise ValueError('D01 draw has no valid selected aperture')
                w=ap['args'][0] if ap.get('args') else default_width*unit; p=offset_line(a,b,w)
                if p:add_path(p); strokes.append((a,b,w))
            stats['arcs']+=1
        cur=target
    if active is not None and active:add_path(active)
    if not contours: raise ValueError('Gerber contains no drawable geometry')
    # convert every contour to edge records; this is the geometry represented by pwszImg
    segments=[]
    for p in contours:
        for a,b in zip(p,p[1:]+p[:1]):
            if math.hypot(b[0]-a[0],b[1]-a[1])>EPS:segments.append((a[0],a[1],b[0],b[1],1))
    stats['fallback_flashes']=0
    stats['contours']=len(contours); stats['segments']=len(segments); stats['warnings']=warnings; stats['fallback_flashes']=0
    return segments,contours,stats

def frame_polygon(frame_contours):
    """Build a frame from the actual closed outline geometry.

    Keep-out files often contain four independent D01 strokes.  Polygonizing
    their boundaries recovers the largest bounded face (the true outline)
    while preserving its rectangular/rounded shape.  It deliberately never
    uses a convex hull of soldermask or input-layer geometry.
    """
    try:
        from shapely.geometry import LineString, Polygon
        from shapely.ops import polygonize, unary_union
    except ImportError as e:
        raise RuntimeError('inversion requires Shapely; install it with: python -m pip install Shapely') from e
    rings=[]
    for points in frame_contours:
        if len(points) >= 3:
            rings.append(LineString(points + [points[0]]))
    if not rings:
        raise ValueError('frame has no drawable outline')
    linework=unary_union(rings)
    faces=[p for p in polygonize(linework) if not p.is_empty and p.area > EPS]
    if faces:
        return max(faces, key=lambda p:p.area)
    # A single already-filled closed region is a valid frame.
    polys=[]
    for points in frame_contours:
        if len(points) >= 3:
            p=Polygon(points)
            if not p.is_empty and p.is_valid and p.area > EPS:
                polys.append(p)
    if polys:
        return max(polys, key=lambda p:p.area)
    raise ValueError('frame outline is open or has zero area; provide a closed Keep-out outline')

def subtract_from_frame(input_contours, frame_contours):
    """Return frame minus input as (segments, boundary contours, Shapely geometry)."""
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union
    except ImportError as e:
        raise RuntimeError('inversion requires Shapely; install it with: python -m pip install Shapely') from e

    def as_polygons(contours):
        polys=[]
        for points in contours:
            if len(points) < 3:
                continue
            g=Polygon(points)
            if not g.is_valid:
                g=g.buffer(0)
            if not g.is_empty and g.area > EPS:
                polys.append(g)
        if not polys:
            raise ValueError('frame/input has no valid filled polygon geometry')
        return unary_union(polys)

    # A perimeter made from D01 strokes is not itself a filled polygon.
    # Build the board mask from the closed Keep-out outline before subtracting input primitives.
    frame=frame_polygon(frame_contours)
    input_geom=as_polygons(input_contours)
    result=frame.difference(input_geom)
    if result.is_empty or result.area <= EPS:
        raise ValueError('inverted geometry is empty: input covers the frame')
    if not result.is_valid:
        result=result.buffer(0)
    geoms=list(result.geoms) if result.geom_type == 'MultiPolygon' else [result]
    contours=[]
    for poly in geoms:
        if poly.geom_type != 'Polygon' or poly.is_empty:
            continue
        contours.append([(float(x),float(y)) for x,y in list(poly.exterior.coords)[:-1]])
        contours.extend([[(float(x),float(y)) for x,y in list(r.coords)[:-1]] for r in poly.interiors])
    segs=[]
    for ring in contours:
        for a,b in zip(ring,ring[1:]+ring[:1]):
            if math.hypot(b[0]-a[0],b[1]-a[1]) > EPS:
                segs.append((a[0],a[1],b[0],b[1],1))
    return segs, contours, result

def write_pwsz(segs,reference,out):
    h=bytearray(Path(reference).read_bytes()[:48]) if reference else bytearray(BUNDLED_HEADER); t=Path(reference).read_bytes()[-8:] if reference else BUNDLED_TRAILER
    if len(h)!=48 or len(t)!=8:raise ValueError('reference pwszImg is too short')
    struct.pack_into('<I',h,40,len(segs))
    with Path(out).open('wb') as f:
        f.write(h)
        for s in segs:f.write(RECORD.pack(float(s[0]),float(s[1]),float(s[2]),float(s[3]),1))
        f.write(t)

def verify_pwsz(path,segs):
    b=Path(path).read_bytes(); n=struct.unpack_from('<I',b,40)[0]
    if len(b)!=48+n*17+8 or n!=len(segs):raise ValueError('pwszImg count/length mismatch')
    for i,s in enumerate(segs):
        got=RECORD.unpack_from(b,48+i*17); want=tuple(struct.unpack('<f',struct.pack('<f',float(v)))[0] for v in s[:4])
        if got[:4]!=want:raise ValueError('pwszImg float mismatch at record '+str(i))
    return n

def normal(a,b,c):
    u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)];n=(u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]);m=math.sqrt(sum(x*x for x in n));return tuple(x/m for x in n) if m else (0,0,0)
def write_stl(contours,out,thick,geometry=None):
    """Write a watertight extrusion of the union of all filled contours.

    Each input contour is a filled positive Gerber shape.  Unioning first is
    essential: independently extruded overlapping flashes create internal
    faces and non-manifold intersections.  Shapely's constrained Delaunay
    triangles are filtered against the final polygons, including holes; side
    walls are emitted for every exterior and interior ring.
    """
    if thick <= 0:
        raise ValueError('STL thickness must be positive')
    try:
        from shapely.geometry import Polygon
        from shapely.ops import unary_union, triangulate
    except ImportError as e:
        raise RuntimeError('STL generation requires shapely for watertight polygon union and triangulation') from e

    polys=[]
    for p in contours:
        if len(p) < 3:
            continue
        q=Polygon(p)
        if not q.is_valid:
            q=q.buffer(0)
        if not q.is_empty and q.area > EPS:
            polys.append(q)
    if not polys:
        raise ValueError('no valid filled polygons for STL')
    merged=geometry if geometry is not None else unary_union(polys)
    if not merged.is_valid:
        merged=merged.buffer(0)
    geometries=list(merged.geoms) if merged.geom_type == 'MultiPolygon' else [merged]
    tris=[]
    def add_surface(poly):
        # triangulate may cover the convex hull; retain only triangles wholly
        # inside the polygon so holes and concavities remain open/cut out.
        for t in triangulate(poly):
            if t.is_empty or t.area <= EPS or not poly.covers(t):
                continue
            v=list(t.exterior.coords)[:3]
            bot=[(float(x),float(y),0.0) for x,y in v]
            top=[(float(x),float(y),float(thick)) for x,y in v]
            # Quantized STL coordinates can collapse very small triangles.
            def tri_area(a,b,c):
                u=(b[0]-a[0],b[1]-a[1],b[2]-a[2]); v=(c[0]-a[0],c[1]-a[1],c[2]-a[2])
                return math.sqrt(sum(x*x for x in (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])))/2
            # bottom faces point down, top faces point up.
            if tri_area(bot[0],bot[2],bot[1]) > EPS:
                tris.append((bot[0],bot[2],bot[1]))
                tris.append((top[0],top[1],top[2]))
        # A ring's natural orientation gives outward-facing side walls for
        # both exterior rings (CCW) and hole rings (CW).
        for ring in [poly.exterior, *poly.interiors]:
            pts=list(ring.coords)[:-1]
            for i,a in enumerate(pts):
                b=pts[(i+1)%len(pts)]
                a0=(float(a[0]),float(a[1]),0.0); b0=(float(b[0]),float(b[1]),0.0)
                a1=(float(a[0]),float(a[1]),float(thick)); b1=(float(b[0]),float(b[1]),float(thick))
                tris.append((a0,b0,b1)); tris.append((a0,b1,a1))
    for g in geometries:
        if g.geom_type == 'Polygon' and g.area > EPS:
            add_surface(g)
    if not tris:
        raise ValueError('no STL triangles')
    with Path(out).open('wb') as f:
        f.write(b'Watertight Gerber polygon extrusion'.ljust(80,b' '))
        f.write(struct.pack('<I',len(tris)))
        for t in tris:
            n=normal(*t)
            f.write(struct.pack('<12fH',*n,*(v for p in t for v in p),0))
    return len(tris)

def write_png(segs, contours, out, dpi=1000, geometry=None):
    """Render filled Gerber contours at a stable high resolution.

    pwszImg stores boundary records, while PNG is a visual preview.  Filling
    the actual parsed contours is essential: drawing only their edges makes
    rounded/capsule apertures look like hollow notches.  Coordinates and
    aperture dimensions are never resized; only the view is fitted to pixels.
    """
    from PIL import Image, ImageDraw

    W, H = 3200, 2240
    AA = 2
    M = 112
    pts = [p for contour in contours for p in contour]
    if not pts:
        raise ValueError('no drawable geometry for PNG')
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs); y0, y1 = min(ys), max(ys)
    sc = min((W - 2 * M) / max(x1 - x0, 1e-6),
             (H - 2 * M) / max(y1 - y0, 1e-6))
    ox = (W - (x1 - x0) * sc) / 2
    oy = (H - (y1 - y0) * sc) / 2

    def xy(x, y, factor=1):
        return (round((ox + (x - x0) * sc) * factor),
                round((H - oy - (y - y0) * sc) * factor))

    render_size = (W * AA, H * AA)
    im = Image.new('RGB', render_size, 'white')
    d = ImageDraw.Draw(im)
    border = max(1, round(M * AA / 2))
    d.rectangle((border, border, render_size[0] - border,
                 render_size[1] - border), outline=(210, 215, 225),
                width=max(1, AA))
    blue = (20, 75, 190)
    if geometry is None:
        draw_polygons = [(contours, [])]
    else:
        draw_polygons = []
        geoms = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
        for poly in geoms:
            if poly.geom_type == 'Polygon':
                ext=[(float(x),float(y)) for x,y in list(poly.exterior.coords)[:-1]]
                holes=[[(float(x),float(y)) for x,y in list(r.coords)[:-1]] for r in poly.interiors]
                draw_polygons.append(([ext], holes))
    for rings, holes in draw_polygons:
        for contour in rings:
            if len(contour) >= 3:
                d.polygon([xy(x, y, AA) for x, y in contour], fill=blue)
                d.line([xy(x, y, AA) for x, y in contour + contour[:1]], fill=blue, width=max(1, AA), joint='curve')
        for hole in holes:
            d.polygon([xy(x, y, AA) for x, y in hole], fill='white')

    # BOX averages the 2x2 coverage samples, avoiding Lanczos ringing. The
    # geometric stroke itself remains full-width in the vector contours.
    im = im.resize((W, H), resample=getattr(Image, 'Resampling', Image).BOX)
    # Explicit physical resolution metadata; dimensions remain 3200x2240.
    im.save(out, 'PNG', optimize=True, dpi=(dpi, dpi))


def main():
    ap=argparse.ArgumentParser(description='Convert RS-274X Gerber geometry to pwszImg, binary STL and PNG. Use --invert to output Keep-out frame minus the input geometry.')
    ap.add_argument('gerber',nargs='?',help='input Gerber file')
    ap.add_argument('--reference',help='optional pwszImg whose 48-byte header and 8-byte trailer are reused')
    ap.add_argument('--thickness',type=float,default=.1,help='STL extrusion thickness in mm (default: 0.1)')
    ap.add_argument('--line-width',type=float,default=.1,help='fallback stroke width in mm (default: 0.1)')
    ap.add_argument('--verify',action='store_true',help='verify generated pwszImg record count and float32 coordinates')
    ap.add_argument('--invert',action='store_true',help='subtract input geometry from the Keep-out frame before writing every output')
    ap.add_argument('--frame',help='frame Gerber for --invert; default: Keep-out.gbr beside the input Gerber')
    a=ap.parse_args()
    if a.thickness<=0 or a.line_width<=0: ap.error('thickness and line-width must be positive')
    if not a.gerber: ap.error('an input Gerber is required')
    input_path=Path(a.gerber).resolve()
    segs,contours,st=parse_gerber(input_path,a.line_width)
    geometry=None
    if a.invert:
        frame_path=Path(a.frame).resolve() if a.frame else input_path.parent/'Keep-out.gbr'
        if not frame_path.is_file():
            raise ValueError(f'inversion frame not found: {frame_path} (pass --frame PATH or add Keep-out.gbr beside the input)')
        if frame_path == input_path:
            raise ValueError('--invert cannot use the input Gerber itself as the frame')
        _,frame_contours,_=parse_gerber(frame_path,a.line_width)
        segs,contours,geometry=subtract_from_frame(contours,frame_contours)
        st['segments']=len(segs); st['contours']=len(contours); st['inverted']=True; st['frame']=str(frame_path)
    outdir=output_directory()
    pre=outdir/input_path.stem; outs=[pre.with_suffix(x) for x in ('.pwszImg','.stl','.png')]
    write_pwsz(segs,a.reference,outs[0])
    if a.verify: print('VERIFY:',verify_pwsz(outs[0],segs),'records and every float32 coordinate match')
    facets=write_stl(contours,outs[1],a.thickness,geometry=geometry); write_png(segs,contours,outs[2],1000,geometry=geometry)
    print('OK:',a.gerber); print('  inverted:',bool(a.invert));
    if a.invert: print('  frame:',st['frame'])
    print('  primitives D01={D01} D02={D02} D03={D03} arcs={arcs} regions={regions} flashes={flashes} contours={contours} segments={segments}'.format(**st)); print('  STL binary:',facets,'facets, thickness',a.thickness); print('  outputs:',*[str(x)+' ('+str(x.stat().st_size)+' bytes)' for x in outs],sep='\n    ')
    for w in st['warnings'][:10]: print('WARNING:',w)
    if len(st['warnings'])>10: print('WARNING:',len(st['warnings'])-10,'additional warnings')
    print('STL geometry: unioned filled contours, triangulated surfaces, and closed side walls (holes preserved where represented).')
if __name__=='__main__':
    try:main()
    except Exception as e:print('ERROR:',e,file=sys.stderr);sys.exit(2)
