import os
import copy
import shlex
import tempfile
import subprocess as sp

from pylab import *
import numpy as np
from scipy.spatial import cKDTree

from lxml import etree
from lxml.builder import E

from cortex.options import config

from svgsplines import *

svgns = "http://www.w3.org/2000/svg"
inkns = "http://www.inkscape.org/namespaces/inkscape"
sodins = "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd"
parser = etree.XMLParser(remove_blank_text=True, huge_tree=True)

cwd = os.path.abspath(os.path.split(__file__)[0])

class ROIpack(object):
    def __init__(self, tcoords, svgfile, callback=None, 
        linewidth=None, linecolor=None, roifill=None, shadow=None,
        labelsize=None, labelcolor=None,layer='rois'):
        self.all_splines = [] # houses the svg splines for a given area
        if np.any(tcoords.max(0) > 1) or np.any(tcoords.min(0) < 0):
            tcoords -= tcoords.min(0)
            tcoords /= tcoords.max(0)

        self.tcoords = tcoords
        self.svgfile = svgfile
        self.callback = callback
        self.kdt = cKDTree(tcoords)
        self.layer = layer 

        self.linewidth = float(config.get(self.layer, "line_width")) if linewidth is None else linewidth
        self.linecolor = tuple(map(float, config.get(self.layer, "line_color").split(','))) if linecolor is None else linecolor
        self.roifill = tuple(map(float, config.get(self.layer, "fill_color").split(','))) if roifill is None else roifill
        self.shadow = float(config.get(self.layer, "shadow")) if shadow is None else shadow
        self.reload(size=labelsize, color=labelcolor)

    def reload(self, **kwargs):
        self.svg = scrub(self.svgfile)
        self.svg = _strip_top_layers(self.svg,self.layer)
        w = float(self.svg.getroot().get("width"))
        h = float(self.svg.getroot().get("height"))
        self.svgshape = w, h

        #Set up the ROI dict
        self.rois = {}
        for r in _find_layer(self.svg, self.layer).findall("{%s}g"%svgns):
            roi = ROI(self, r)
            self.rois[roi.name] = roi

        self.set()
        #self.setup_labels(**kwargs)

    def add_roi(self, name, pngdata, add_path=True):
        """Adds projected data for defining a new ROI to the saved rois.svg file in a new layer"""
        #self.svg deletes the images -- we want to save those, so let's load it again
        svg = etree.parse(self.svgfile)
        imglayer = _find_layer(svg, "data")
        if add_path:
            _make_layer(_find_layer(svg, "rois"), name)

        #Hide all the other layers in the image
        for layer in imglayer.findall(".//{%s}g"%svgns):
            layer.attrib["style"] = "display:hidden;"

        layer = _make_layer(imglayer, "img_%s"%name)
        layer.append(E.image(
            {"{http://www.w3.org/1999/xlink}href":"data:image/png;base64,%s"%pngdata},
            id="image_%s"%name, x="0", y="0",
            width=str(self.svgshape[0]),
            height=str(self.svgshape[1]),
        ))

        with open(self.svgfile, "w") as xml:
            xml.write(etree.tostring(svg, pretty_print=True))

    def set(self, linewidth=None, linecolor=None, roifill=None, shadow=None):
        if linewidth is not None:
            self.linewidth = linewidth
        if linecolor is not None:
            self.linecolor = linecolor
        if roifill is not None:
            self.roifill = roifill
        if shadow is not None:
            self.shadow = shadow
            self.svg.find("//{%s}feGaussianBlur"%svgns).attrib["stdDeviation"] = str(shadow)

        for roi in list(self.rois.values()):
            roi.set(linewidth=self.linewidth, linecolor=self.linecolor, roifill=self.roifill, shadow=shadow)

        try:
            if self.callback is not None:
                self.callback()
        except:
            print("cannot callback")

    def get_svg(self, filename=None, labels=True, with_ims=None, **kwargs):
        """Returns an SVG with the included images."""
        if labels:
            if hasattr(self, "labels"):
                self.labels.attrib['style'] = "display:inline;"
            else:
                self.setup_labels(**kwargs)
        else:
            if hasattr(self, "labels"):
                self.labels.attrib['style'] = "display:none;"
        
        outsvg = copy.deepcopy(self.svg)
        if with_ims is not None:
            if isinstance(with_ims, (list, tuple)):
                with_ims = zip(range(len(with_ims)), with_ims)

            datalayer = _make_layer(outsvg.getroot(), "data")
            for imnum,im in reversed(with_ims):
                imlayer = _make_layer(datalayer, "image_%d" % imnum)
                img = E.image(
                    {"{http://www.w3.org/1999/xlink}href":"data:image/png;base64,%s"%im},
                    id="image_%d"%imnum, x="0", y="0",
                    width=str(self.svgshape[0]),
                    height=str(self.svgshape[1]),
                    )
                imlayer.append(img)
                outsvg.getroot().insert(0, imlayer)
        
        if filename is None:
            return etree.tostring(outsvg)
        else:
            with open(filename, "w") as outfile:
                outfile.write(etree.tostring(outsvg))
        
    def get_texture(self, texres, name=None, background=None, labels=True, bits=32, **kwargs):
        '''Renders the current roimap as a png'''
        #set the current size of the texture
        w, h = self.svgshape
        dpi = texres / h * 72

        if background is not None:
            img = E.image(
                {"{http://www.w3.org/1999/xlink}href":"data:image/png;base64,%s"%background},
                id="image_%s"%name, x="0", y="0",
                width=str(self.svgshape[0]),
                height=str(self.svgshape[1]),
            )
            self.svg.getroot().insert(0, img)

        if labels:
            if hasattr(self, "labels"):
                self.labels.attrib['style'] = "display:inline;"
            else:
                self.setup_labels(**kwargs)
        else:
            if hasattr(self, "labels"):
                self.labels.attrib['style'] = "display:none;"

        pngfile = name
        if name is None:
            png = tempfile.NamedTemporaryFile(suffix=".png")
            pngfile = png.name

        cmd = "convert -background none -density {dpi} SVG:- PNG{bits}:{outfile}"
        cmd = cmd.format(dpi=dpi, outfile=pngfile, bits=bits)
        proc = sp.Popen(shlex.split(cmd), stdin=sp.PIPE, stdout=sp.PIPE)
        proc.communicate(etree.tostring(self.svg))
        
        if background is not None:
            self.svg.getroot().remove(img)

        if name is None:
            png.seek(0)
            return png

    def get_labelpos(self, pts=None, norms=None, fancy=True):
        return dict([(name, roi.get_labelpos(pts, norms, fancy)) for name, roi in list(self.rois.items())])

    def get_ptidx(self):
        return dict([(name, roi.get_ptidx()) for name, roi in list(self.rois.items())])

    def get_splines(self, roiname):
        import svgsplines

        path_strs = [list(_tokenize_path(path.attrib['d']))
                     for path in self.rois[roiname].paths]

        COMMANDS = set('MmZzLlHhVvCcSsQqTtAa')
        UPPERCASE = set('MZLHVCSQTA')
        all_splines = [] # after this gets populated, set self.all_splines = all_splines

        ###
        # this is for the svg path parsing (https://developer.mozilla.org/en-US/docs/Web/SVG/Tutorial/Paths)
        # the general format is that there is a state machine that keeps track of which command (path_ind)
        # that it's listening to while parsing over the appropriately sized (param_len) groups of
        # coordinates for that command 
        ###
        for path in path_strs:
            path_splines = []
            first_coord = array([0,0])
            prev_coord = array([0,0])
            isFirstM = True# inkscape may create multiple starting commands to move to the spline's starting coord, this just treats those as one commend

            for path_ind in range(len(path)):
                if path_ind == 0 and path[path_ind].lower() != 'm':
                    raise ValueError('Unknown path format!')
                
                elif path[path_ind].lower() == 'm': 
                    param_len = 2
                    p_j = path_ind + 1 # temp index
                    
                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'M':
                            prev_coord = array([float(path[p_j]),float(path[p_j+1])])
                        else:
                            prev_coord = prev_coord + array([float(path[p_j]),float(path[p_j+1])])

                        # this conditional is for recognizing and storing the last coord in the first M command(s)
                        # as the official first coord in the spline path for any return-to-first command 
                        if isFirstM == True and (len(COMMANDS.intersection(path[p_j+param_len].lower())) == 1 and
                                                 COMMANDS.intersection(path[p_j+param_len].lower()) != 'm'):
                            prev_coord[1] = self.svgshape[1] - prev_coord[1] # starting coord needs to be referenced from the flipped origin
                            first_coord = prev_coord
                            isFirstM = False

                        p_j += param_len
                        
                elif path[path_ind].lower() == 'z':
                    path_splines.append(LineSpline(prev_coord, first_coord))                    
                    prev_coord = first_coord
                    
                elif path[path_ind].lower() == 'l':
                    param_len = 2
                    p_j = path_ind + 1
                    next_coord = array([0,0])
                                       
                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'L':
                            next_coord = array([float(path[p_j]),float(path[p_j+1])])

                        else:
                            next_coord = prev_coord + array([float(path[p_j]),-1.0*float(path[p_j+1])]) 
                        
                        path_splines.append(LineSpline(prev_coord, next_coord))
                        prev_coord = next_coord
                        p_j += param_len

                elif path[path_ind].lower() == 'h':
                    param_len = 1
                    p_j = path_ind + 1
                    next_coord = array([0,0])
                                       
                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'H':
                            next_coord = array([float(path[p_j]), prev_coord[1]])
                        else:
                            next_coord = prev_coord + array([float(path[p_j]), 0])
                        
                        path_splines.append(LineSpline(prev_coord, next_coord))
                        prev_coord = next_coord
                        p_j += param_len
                
                elif path[path_ind].lower() == 'v':
                    param_len = 1
                    p_j = path_ind + 1
                    next_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'V':
                            next_coord = array([prev_coord[0], float(path[p_j])])
                        else:
                            next_coord = prev_coord + array([0, -1.0*float(path[p_j])]) 
                        
                        path_splines.append(LineSpline(prev_coord, next_coord))
                        prev_coord = next_coord
                        p_j += param_len
                
                elif path[path_ind].lower() == 'c':
                    param_len = 6
                    p_j = path_ind + 1
                    ctl1_coord = array([0,0])
                    ctl2_coord = array([0,0])
                    end_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'C':
                            ctl1_coord = array([float(path[p_j]), float(path[p_j+1])])
                            ctl2_coord = array([float(path[p_j+2]), float(path[p_j+3])])
                            end_coord = array([float(path[p_j+4]), float(path[p_j+5])])
                        else:
                            ctl1_coord = prev_coord + array([float(path[p_j]), -1.0*float(path[p_j+1])]) 
                            ctl2_coord = prev_coord + array([float(path[p_j+2]), -1.0*float(path[p_j+3])]) 
                            end_coord = prev_coord + array([float(path[p_j+4]), -1.0*float(path[p_j+5])]) 

                        path_splines.append(CubBezSpline(prev_coord, ctl1_coord, ctl2_coord, end_coord))
                        prev_coord = end_coord
                        p_j += param_len
                
                elif path[path_ind].lower() == 's':
                    param_len = 4
                    p_j = path_ind + 1
                    ctl1_coord = array([0,0])
                    ctl2_coord = array([0,0])
                    end_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        ctl1_coord = prev_coord - path_splines[len(path_splines)-1].c2 + prev_coord

                        if path[path_ind] == 'S':
                            ctl2_coord = array([float(path[p_j]), float(path[p_j+1])])
                            end_coord = array([float(path[p_j+2]), float(path[p_j+3])])
                        else:
                            ctl2_coord = prev_coord + array([float(path[p_j]), -1.0*float(path[p_j+1])]) 
                            end_coord = prev_coord + array([float(path[p_j+2]), -1.0*float(path[p_j+3])]) 
                        
                        path_splines.append(CubBezSpline(prev_coord, ctl1_coord, ctl2_coord, end_coord))
                        prev_coord = end_coord
                        p_j += param_len

                elif path[path_ind].lower() == 'q':
                    param_len = 4
                    p_j = path_ind + 1
                    ctl_coord = array([0,0])
                    end_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        if path[path_ind] == 'Q':
                            ctl_coord = array([float(path[p_j]), float(path[p_j+1])])
                            end_coord = array([float(path[p_j+2]), float(path[p_j+3])])
                        else:
                            ctl_coord = prev_coord + array([float(path[p_j]), -1.0*float(path[p_j+1])]) 
                            end_coord = prev_coord + array([float(path[p_j+2]), -1.0*float(path[p_j+3])]) 
                                        
                        path_splines.append(QuadBezSpline(prev_coord, ctl_coord, end_coord))
                        prev_coord = end_coord
                        p_j += param_len
                
                elif path[path_ind].lower() == 't':
                    param_len = 2
                    p_j = path_ind + 1
                    ctl_coord = array([0,0])
                    end_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        ctl_coord = prev_coord - path_splines[len(path_splines)-1].c + prev_coord

                        if path[path_ind] == 'T':
                            end_coord = array([float(path[p_j]), float(path[p_j+1])])
                        else:
                            end_coord = prev_coord + array([float(path[p_j]), -1.0*float(path[p_j+1])]) 

                        path_splines.append(QuadBezSpline(prev_coord, ctl_coord, end_coord))
                        prev_coord = end_coord
                        p_j += param_len
                
                # NOTE: This is *NOT* functional. Arcspline parsing saves to an incomplete ArcSpline class
                elif path[path_ind].lower() == 'a': 
                    param_len = 7
                    p_j = path_ind + 1
                    end_coord = array([0,0])

                    while p_j < len(path) and len(COMMANDS.intersection(path[p_j])) == 0:
                        rx = float(path[p_j])
                        ry = float(path[p_j+1])
                        x_rot = float(path[p_j+2])
                        large_arc_flag = int(path[p_j+3])
                        sweep_flag = int(path[p_j+4])

                        if path[path_ind] == 'A':
                            end_coord = array([float(path[p_j+5]), float(path[p_j+6])])
                        else:
                            end_coord = prev_coord + array([float(path[p_j+5]), -1.0*float(path[p_j+6])])

                        path_splines.append(ArcSpline(prev_coord, rx, ry, x_rot, large_arc_flag, sweep_flag, end_coord))
                        prev_coord = end_coord
                        p_j += param_len

            all_splines.append(path_splines)

        return all_splines


    ###
    # The get_roi function takes in an roi's name and returns an array indicating if every vertex is or isn't in that roi
    # The way it works is that it collapses all of the x-values of the vertex coordinates approximately around the roi to the same
    # small value, making a vertical line left of the roi. Then, it stretches the line to the right again, but stops the coordinates if they
    # hit either an roi boundary or the original vertex position. In other words, it increases the x-values of the coordinates to either
    # those of the the nearest spline path or the original vertex coordinate, whichever has the closer x-value.
    # This way, it keeps track of how many boundaries it hit, starting from the outside, going inward toward the the original vertex coordinate.
    # An odd number of boundaries found before the vertex means 'outside' the region or 'False' in the array, and an even number of
    # boundaries found before the vertex means 'inside' the region or 'True' in the array
    #
    # This is all implemented with 1d and nd arrays manipulations, so the math is very algebraic.
    ###
    def get_roi(self, roiname):
        vts = self.tcoords*self.svgshape # reverts tcoords from unit circle size to normal svg image format size  
        all_splines = self.get_splines(roiname) #all_splines is a list of generally two roi paths, one for each hemisphere

        vts_inside_region = zeros(vts.shape[0],dtype=bool) # ultimately what gets returned

        for splines in all_splines: #retrieves path splines for each hemisphere separately
            x0s = min(vts[:,0])*.98*ones(vts.shape[0])

            # Only checks the vertices in a bounding box around the spline path.
            # The splines are always within a convex shape whose corners are
            # their svg command's end point and control points, so the box is their
            # min and max X and Y coordinates.
            beforeSplineRegionX = vts[:,0] < min([float(sp_i.smallestX()) for sp_i in splines])
            beforeSplineRegionY = vts[:,1] < min([float(sp_i.smallestY()) for sp_i in splines])
            afterSplineRegionX = vts[:,0] > max([float(sp_i.biggestX()) for sp_i in splines])
            afterSplineRegionY = vts[:,1] > max([float(sp_i.biggestY()) for sp_i in splines])

            found_vtxs = zeros(vts.shape[0],dtype=bool)
            found_vtxs[beforeSplineRegionX] = True
            found_vtxs[beforeSplineRegionY] = True
            found_vtxs[afterSplineRegionX] = True
            found_vtxs[afterSplineRegionY] = True
            
            vt_isx = vstack([x0s,vts[:,1]]).T #iterable coords, same x-value as each other, but at their old y-value positions
 
            vtx_is = vt_isx[~found_vtxs]

            splines_xs = [] # stores the roi's splines
            for i in range(len(splines)):
                splines_xs.append(splines[i].allSplineXsGivenY(vtx_is)) # gets all the splines' x-values for each y-value in the line we're checking

            small_vts = vts[~found_vtxs,:]
            small_vts_inside_region = vts_inside_region[~found_vtxs]
            small_found_vtxs = found_vtxs[~found_vtxs]

            # keeps stretching the vertical line to the right until all the points find their original vertex again
            while sum(small_found_vtxs) != len(small_found_vtxs):
                closest_xs = Inf*ones(vtx_is.shape[0]) # starting marker for all vts are at Inf

                for spline_i_xs in splines_xs:
                    if len(spline_i_xs.shape) == 1: # Line splines
                        isGreaterThanVtx = spline_i_xs > vtx_is[:,0]
                        isLessThanClosestX = spline_i_xs < closest_xs
                        closest_xs[isGreaterThanVtx*isLessThanClosestX] = spline_i_xs[isGreaterThanVtx*isLessThanClosestX]
                    else: # all other splines
                        for j in range(spline_i_xs.shape[1]):
                            isGreaterThanVtx = spline_i_xs[:,j] > vtx_is[:,0]
                            isLessThanClosestX = spline_i_xs[:,j] < closest_xs
                            closest_xs[isGreaterThanVtx*isLessThanClosestX] = spline_i_xs[isGreaterThanVtx*isLessThanClosestX,j]

                # checks if it's found the boundary or the original vertex
                # it forgets about all the points in the line who've found their original vertex
                # if it found a boundary, then flip the 'inside' flag to 'outside', and vice versa
                small_found_vtxsx = small_vts[~small_found_vtxs,0]<closest_xs
                small_found_vtxs[~small_found_vtxs] = small_found_vtxsx

                small_vts_inside_region[~small_found_vtxs] = True - small_vts_inside_region[~small_found_vtxs]
                vtx_is[~small_found_vtxsx,0] = closest_xs[~small_found_vtxsx]
                vtx_is = vtx_is[~small_found_vtxsx,:]

                for i in range(len(splines_xs)):
                    if len(splines_xs[i].shape) == 1:
                        splines_xs[i] = splines_xs[i][~small_found_vtxsx]
                    else:
                        splines_xs[i] = splines_xs[i][~small_found_vtxsx,:]

            vts_inside_region[~found_vtxs] = small_vts_inside_region # reverts shape back from small bounding box to whole brain shape

            if sum(vts_inside_region) == len(vts_inside_region):
                break

        return vts_inside_region # final output of whether or not each brain vertex is within the specified roi
    
    @property
    def names(self):
        return list(self.rois.keys())

    def __getitem__(self, name):
        return self.rois[name]

    def setup_labels(self, size=None, color=None, shadow=None):
        if size is None:
            size = config.get("rois", "labelsize")
        if color is None:
            color = tuple(map(float, config.get("rois", "labelcolor").split(",")))
        if shadow is None:
            shadow = self.shadow

        alpha = color[3]
        color = "rgb(%d, %d, %d)"%(color[0]*255, color[1]*255, color[2]*255)

        try:
            layer = _find_layer(self.svg, "roilabels")
        except AssertionError:
            layer = _make_layer(self.svg.getroot(), "roilabels")

        labelpos, candidates = [], []
        for roi in list(self.rois.values()):
            for i, pos in enumerate(roi.get_labelpos()):
                labelpos.append(pos)
                candidates.append((roi, i))

        w, h = self.svgshape
        nolabels = set(candidates)
        txtstyle = "font-family:sans;font-size:%s;font-weight:bold;font-style:italic;fill:%s;fill-opacity:%f;text-anchor:middle;"%(size, color, alpha)
        for text in layer.findall(".//{%s}text"%svgns):
            x = float(text.get('x'))
            y = float(text.get('y'))
            text.attrib['style'] = txtstyle
            text.attrib['data-ptidx'] = str(self.kdt.query((x / w, 1-(y / h)))[1])
            pts, cand = [], []
            for p, c in zip(labelpos, candidates):
                if c[0].name == text.text:
                    pts.append((p[0]*w, (1-p[1])*h))
                    cand.append(c)
            d, idx = cKDTree(pts).query((x,y))
            nolabels.remove(cand[idx])

        for roi, i in nolabels:
            x, y = roi.get_labelpos()[i]
            text = etree.SubElement(layer, "{%s}text"%svgns)
            text.text = roi.name
            text.attrib["x"] = str(x*w)
            text.attrib["y"] = str((1-y)*h)
            if self.shadow > 0:
                text.attrib['filter'] = "url(#dropshadow)"
            text.attrib['style'] = txtstyle
            text.attrib['data-ptidx'] = str(self.kdt.query((x, y))[1])

        self.labels = layer
        return layer

    def toxml(self, pretty=True):
        return etree.tostring(self.svg, pretty_print=pretty)


class ROI(object):
    def __init__(self, parent, xml):
        self.parent = parent
        self.name = xml.get("{%s}label"%inkns)
        self.paths = xml.findall(".//{%s}path"%svgns)
        self.hide = "style" in xml.attrib and "display:none" in xml.get("style")
        self.set(linewidth=self.parent.linewidth, linecolor=self.parent.linecolor, roifill=self.parent.roifill)
    
    def _parse_svg_pts(self, datastr):
        data = list(_tokenize_path(datastr))
        #data = data.replace(",", " ").split()
        if data.pop(0).lower() != "m":
            raise ValueError("Unknown path format")
        #offset = np.array([float(x) for x in data[1].split(',')])
        offset = np.array(map(float, [data.pop(0), data.pop(0)]))
        mode = "l"
        pts = [[offset[0], offset[1]]]
        
        def canfloat(n):
            try:
                float(n)
                return True
            except ValueError:
                return False

        lastlen = len(data)
        while len(data) > 0:
            #print mode, data
            if not canfloat(data[0]):
                mode = data.pop(0)
                continue
            if mode == "l":
                offset += list(map(float, [data.pop(0), data.pop(0)]))
            elif mode == "L":
                offset = np.array(list(map(float, [data.pop(0), data.pop(0)])))
            elif mode == "c":
                data = data[4:]
                offset += list(map(float, [data.pop(0), data.pop(0)]))
            elif mode == "C":
                data = data[4:]
                offset = np.array(list(map(float, [data.pop(0), data.pop(0)])))

            ## Check to see if nothing has happened, and, if so, fail
            if len(data) == lastlen:
                raise ValueError("Error parsing path.")
            else:
                lastlen = len(data)

            pts.append([offset[0],offset[1]])

        pts = np.array(pts)
        pts /= self.parent.svgshape
        pts[:,1] = 1-pts[:,1]
        return pts
    
    def set(self, linewidth=None, linecolor=None, roifill=None, shadow=None, hide=None):
        if linewidth is not None:
            self.linewidth = linewidth
        if linecolor is not None:
            self.linecolor = linecolor
        if roifill is not None:
            self.roifill = roifill
        if hide is not None:
            self.hide = hide

        style = "fill:{fill}; fill-opacity:{fo};stroke-width:{lw}px;"+\
                    "stroke-linecap:butt;stroke-linejoin:miter;"+\
                    "stroke:{lc};stroke-opacity:{lo};{hide}"
        roifill = np.array(self.roifill)*255
        linecolor = np.array(self.linecolor)*255
        hide = "display:none;" if self.hide else ""
        style = style.format(
            fill="rgb(%d,%d,%d)"%tuple(roifill[:-1]), fo=roifill[-1],
            lc="rgb(%d,%d,%d)"%tuple(linecolor[:-1]), lo=linecolor[-1], 
            lw=self.linewidth, hide=hide)

        for path in self.paths:
            path.attrib["style"] = style
            if self.parent.shadow > 0:
                path.attrib["filter"] = "url(#dropshadow)"
            elif "filter" in path.attrib:
                del path.attrib['filter']
    
    def get_labelpos(self, pts=None, norms=None, fancy=True):
        if not hasattr(self, "coords"):
            cpts = [self._parse_svg_pts(path.get("d")) for path in self.paths]
            self.coords = [ self.parent.kdt.query(p)[1] for p in cpts ]
        
        if pts is None:
            pts = self.parent.tcoords

        if fancy:
            labels = []
            for coord in self.coords:
                try:
                    if norms is None:
                        labels.append(_labelpos(pts[coord]))
                    else:
                        labels.append((_labelpos(pts[coord]), norms[coord].mean(0)))
                except:
                    if norms is None:
                        labels.append(pts[coord].mean(0))
                    else:
                        labels.append((pts[coord].mean(0), norms[coord].mean(0)))
            return labels

        if norms is None:
            return [pts[coord].mean(0) for coord in self.coords]

        return [(pts[coord].mean(0), norms[coord].mean(0)) for coord in self.coords]

    def get_ptidx(self):
        return self.coords

###################################################################################
# SVG Helper functions
###################################################################################
def _find_layer(svg, label):
    layers = [l for l in svg.findall("//{%s}g[@{%s}label]"%(svgns, inkns)) if l.get("{%s}label"%inkns) == label]
    if len(layers) < 1:
        raise ValueError("Cannot find layer %s"%label)
    return layers[0]

def _make_layer(parent, name):
    layer = etree.SubElement(parent, "{%s}g"%svgns)
    layer.attrib['id'] = name
    layer.attrib['style'] = "display:inline;"
    layer.attrib["{%s}label"%inkns] = name
    layer.attrib["{%s}groupmode"%inkns] = "layer"
    return layer

def _strip_top_layers(svg,layer):
    """Remove all top-level layers except <layer> from lxml svg object"""
    tokeep = _find_layer(svg,layer) # will throw an error if not present
    tostrip = [l for l in svg.getroot().getchildren() if l.get('{%s}label'%inkns) and not l.get('{%s}label'%inkns)==layer
        and not l.get('{%s}label'%inkns)=='roilabels']
    for s in tostrip:
        s.getparent().remove(s)
    return svg
    
try:
    from shapely.geometry import Polygon
    def _center_pts(pts):
        '''Fancy label position generator, using erosion to get label coordinate'''
        min = pts.min(0)
        pts -= min
        max = pts.max(0)
        pts /= max

        #probably don't need more than 20 points, reduce detail of the polys
        if len(pts) > 20:
            pts = pts[::len(pts)/20]

        poly = Polygon([tuple(p) for p in pts])
        for i in np.linspace(0,1,100):
            if poly.buffer(-i).is_empty:
                return list(poly.buffer(-last_i).centroid.coords)[0] * max + min
            last_i = i

        print("unable to find zero centroid...")
        return list(poly.buffer(-100).centroid.coords)[0] * max + min

except ImportError:
    print("Cannot find shapely, using simple label placement")
    def _center_pts(pts):
        return pts.mean(0)

def _labelpos(pts):
    if pts.ndim < 3:
        return _center_pts(pts)

    ptm = pts.copy().astype(float)
    ptm -= ptm.mean(0)
    u, s, v = np.linalg.svd(ptm, full_matrices=False)
    sp = np.diag(s)
    sp[-1,-1] = 0
    try:
        x, y = _center_pts(np.dot(ptm, np.dot(v.T, sp))[:,:2])
    except Exception as e:
        print(e)

    sp = np.diag(1./(s+np.finfo(float).eps))
    pt = np.dot(np.dot(np.array([x,y,0]), sp), v)
    return pt + pts.mean(0)


def scrub(svgfile):
    """Remove data layers from an svg object prior to rendering"""
    svg = etree.parse(svgfile, parser=parser)
    try:
        rmnode = _find_layer(svg, "data")
        rmnode.getparent().remove(rmnode)
    except ValueError:
        pass
    svgtag = svg.getroot()
    svgtag.attrib['id'] = "svgroi"
    inkver = "{%s}version"%inkns
    if inkver in svgtag.attrib:
        del svgtag.attrib[inkver]
    try:
        for tagname in ["{%s}namedview"%sodins, "{%s}metadata"%svgns]:
            for tag in svg.findall(".//%s"%tagname):
                tag.getparent().remove(tag)
    except:
        import traceback
        traceback.print_exc()

    return svg

def make_svg(pts, polys):
    from .polyutils import trace_poly, boundary_edges
    pts = pts.copy()
    pts -= pts.min(0)
    pts *= 1024 / pts.max(0)[1]
    pts[:,1] = 1024 - pts[:,1]
    path = ""
    polyiter = trace_poly(boundary_edges(polys))
    for poly in [polyiter.next(), polyiter.next()]:
        path +="M%f %f L"%tuple(pts[poly.pop(0), :2])
        path += ', '.join(['%f %f'%tuple(pts[p, :2]) for p in poly])
        path += 'Z '

    w, h = pts.max(0)[:2]
    with open(os.path.join(cwd, "svgbase.xml")) as fp:
        svg = fp.read().format(width=w, height=h, clip=path)

    return svg

def get_roipack(svgfile, pts, polys, remove_medial=False, **kwargs):
    from .db import surfs
    
    cullpts = pts[:,:2]
    if remove_medial:
        valid = np.unique(polys)
        cullpts = cullpts[valid]

    if not os.path.exists(svgfile):
        with open(svgfile, "w") as fp:
            fp.write(make_svg(pts.copy(), polys))

    rois = ROIpack(cullpts, svgfile, **kwargs)
    if remove_medial:
        return rois, valid
        
    return rois

## From svg.path (https://github.com/regebro/svg.path/blob/master/src/svg/path/parser.py)
import re

COMMANDS = set('MmZzLlHhVvCcSsQqTtAa')
UPPERCASE = set('MZLHVCSQTA')

COMMAND_RE = re.compile("([MmZzLlHhVvCcSsQqTtAa])")
FLOAT_RE = re.compile("[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")

def _tokenize_path(pathdef):
    for x in COMMAND_RE.split(pathdef):
        if x in COMMANDS:
            yield x
        for token in FLOAT_RE.findall(x):
            yield token
 
