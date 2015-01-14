var dataset = (function(module) {
    module.filtertypes = { 
        nearest: THREE.NearestFilter, 
        trilinear: THREE.LinearFilter, 
        nearlin: THREE.LinearFilter, 
        debug: THREE.NearestFilter 
    };

    module.samplers = {
        trilinear: "trilinear",
        nearest: "nearest",
        nearlin: "nearest",
        debug: "debug",
    };

    module.brains = {}; //Singleton containing all BrainData objects

    module.fromJSON = function(dataset) {
        for (var name in dataset.data) {
            if (dataset.data[name].mosaic === undefined)
                module.brains[name] = new module.VertexData(dataset.data[name], dataset.images);
            else
                module.brains[name] = new module.VolumeData(dataset.data[name], dataset.images);
        }
        var dataviews = [];
        for (var i = 0; i < dataset.views.length; i++) {
            dataviews.push(new module.DataView(dataset.views[i]));
        }
        return dataviews;
    }


    module.makeFrom = function(dvx, dvy) {
        //Generate a new DataView given two existing ones
        var json = {};
        json.name = dvx.name + " vs. "+ dvy.name;
        json.data = [[dvx.data[0].name, dvy.data[0].name]];
        json.description = "2D colormap for "+dvx.name+" and "+dvy.name;
        json.cmap = [viewopts.default_2Dcmap];
        json.vmin = [[dvx.vmin, dvy.vmin]];
        json.vmax = [[dvx.vmax, dvy.vmax]];
        json.attrs = dvx.attrs;
        json.state = dvx.state;
        json.xfm = [[dvx.xfm, dvy.xfm]];
        return new module.DataView(json);
    };

    module.DataView = function(json) {
        this.uuid = THREE.Math.generateUUID();
        this.data = [];
        //Do not handle muliviews for now!
        for (var i = 0; i < json.data.length; i++) {
            if (json.data[i] instanceof Array) {
                this.data.push(module.brains[json.data[i][0]]);
                this.data.push(module.brains[json.data[i][1]]);
            } else {
                this.data.push(module.brains[json.data[i]]);
            }
        }
        this.name = json.name;
        this.description = json.desc;
        this.frames = this.data[0].frames;
        this.length = this.frames / this.rate;
        this.vertex = this.data[0].mosaic === undefined;

        this.attrs = json.attrs;
        this.state = json.state;
        this.loaded = $.Deferred().done(function() { $("#dataload").hide(); });
        this.rate = json.attrs.rate === undefined ? 1 : json.attrs.rate;
        this.delay = json.attrs.delay === undefined ? 0 : json.attrs.delay;
        this.filter = json.attrs.filter === undefined ? "nearest" : json.attrs.filter;
        if (json.attrs.stim !== undefined)
            this.stim = "stim/"+json.attrs.stim;

        this.cmap = [{type:'t', value:colormaps[json.cmap[0]]}];
        this.vmin = [{type:'fv1', value:json.vmin[0] instanceof Array? json.vmin[0] : [json.vmin[0], 0]}];
        this.vmax = [{type:'fv1', value:json.vmax[0] instanceof Array? json.vmax[0] : [json.vmax[0],0]}];

        this.uniforms = {
            framemix:   { type:'f',   value:0},
            dataAlpha:  { type:'f', value:1.0},
        }

        if (!this.vertex) {
            //no multiviews yet
            this.xfm = json.xfm[0];
            //dataview contains volume data
            this.uniforms.data   = { type:'tv',  value:[null, null, null, null]};
            this.uniforms.mosaic = { type:'v2v', value:[new THREE.Vector2(6, 6), new THREE.Vector2(6, 6)]};
            this.uniforms.dshape = { type:'v2v', value:[new THREE.Vector2(100, 100), new THREE.Vector2(100, 100)]};
            this.uniforms.volxfm = { type:'m4v', value:[new THREE.Matrix4(), new THREE.Matrix4()] };
        }

        this._dispatch = this.dispatchEvent.bind(this);

        //Aggregate all Volume/VertexData deferreds to determine when to resolve
        var allready = [];
        for (var i = 0; i < this.data.length; i++) {
            allready.push(false);
        }

        var deferred = this.data.length == 1 ? 
            $.when(this.data[0].loaded) : 
            $.when(this.data[0].loaded, this.data[1].loaded);
        deferred.progress(function(available) {
            for (var i = 0; i < this.data.length; i++) {
                //TODO: fix this load order
                if (available > this.delay && !allready[i]) {
                    allready[i] = true;

                    //Resolve this deferred if ALL the BrainData objects are loaded (for multiviews)
                    var test = true;
                    for (var i = 0; i < allready.length; i++)
                        test = test && allready[i];
                    if (test)
                        this.loaded.resolve();
                }
            }
        }.bind(this)).done(function() {
            this.loaded.resolve();
        }.bind(this));
    }
    THREE.EventDispatcher.prototype.apply(module.DataView.prototype);
    module.DataView.prototype.setVminmax = function(min, max, dim, idx) {
        if (dim === undefined)
            dim = 0;

        if (idx === undefined) {
            for (var i = 0; i < this.data.length; i++) {
                this.vmin[i].value[dim] = min;
                this.vmax[i].value[dim] = max;
            }
        } else {
            this.vmin[idx].value[dim] = min;
            this.vmax[idx].value[dim] = max;
        }
    }
    module.DataView.prototype.setColormap = function(cmap, idx) {
        if (idx === undefined) {
            for (var i = 0; i < this.data.length; i++) {
                this.cmap[i].value = colormaps[cmap];
            }
        } else {
            this.cmap[idx].value = colormaps[cmap];
        }
    }

    module.DataView.prototype.getShader = function(shaderfunc, uniforms, opts) {
        if (this.loaded.state() == "pending")
            $("#dataload").show();

        //This only kind of supports multiviews
        //TODO: hash the multiview as a separate object
        var shaders = [];
        for (var i = 0; i < this.data.length; i++) {
            //Run a shallow merge on the uniform variables
            var merge = {
                colormap: this.cmap[i],
                vmin:     this.vmin[i],
                vmax:     this.vmax[i]
            }
            for (var name in this.uniforms)
                merge[name] = this.uniforms[name];
            for (var name in uniforms)
                merge[name] = uniforms[name];

            opts.sampler = module.samplers[this.filter];
            opts.rgb = this.data[0].raw;
            opts.twod = this.data.length > 1;
            opts.voxline = viewopts.voxlines;
            var shadecode = shaderfunc(opts);
            var shader = new THREE.ShaderMaterial({ 
                vertexShader:shadecode.vertex,
                fragmentShader:shadecode.fragment,
                attributes: shadecode.attrs,
                uniforms: merge,
                // side:THREE.DoubleSide,
                side:THREE.FrontSide, // using frontside almost doubles performance
                lights:true,
                depthTest:opts.depthTest === undefined ? true : opts.depthTest,
                depthWrite:opts.depthWrite === undefined ? true : opts.depthWrite,
                transparent:opts.transparent === undefined ? false : opts.transparent,
                blending:opts.blending === undefined ? THREE.NormalBlending : opts.blending,
                lights:opts.lights === undefined ? true : opts.lights,
            });
            shaders.push(shader);
        }

        return shaders;
    };
    module.DataView.prototype.set = function() {
        for (var i = 0; i < this.data.length; i++) {
            this.data[i].init(this.uniforms, i, this.xfm, this.filter);
        }
        this.setFrame(0);
    };
    module.DataView.prototype.setFrame = function(time) {
        var frame = ((time + this.delay) * this.rate).mod(this.frames);
        var fframe = Math.floor(frame);
        this.uniforms.framemix.value = frame - fframe;
        for (var i = 0; i < this.data.length; i++) {
            this.data[i].set(this.uniforms, i, fframe, this._dispatch);
        }
    }
    module.DataView.prototype.setFilter = function(interp) {
        this.filter = interp;
        for (var i = 0; i < this.data.length; i++)
            this.data[i].setFilter(interp);
        //force a shader update for all surfaces using this dataview
        this.dispatchEvent({type:"update", dataview:this});
    };

    module.VolumeData = function(json, images) {
        this.loaded = $.Deferred();
        this.subject = json.subject;
        this.movie = images[json.name].length > 1;
        this.raw = json.raw;
        this.min = json.min;
        this.max = json.max;
        this.mosaic = json.mosaic;
        this.name = json.name;
        this.shape = json.shape;
        this.data = images[json.name];
        this.frames = images[json.name].length;

        this.textures = [];
        var loadmosaic = function(idx) {
            var img = new Image();
            img.addEventListener("load", function() {
                var tex;
                if (this.raw) {
                    tex = new THREE.Texture(img);
                    tex.premultiplyAlpha = true;
                } else {
                    var canvas = document.createElement("canvas");
                    var ctx = canvas.getContext('2d');
                    canvas.width = img.width;
                    canvas.height = img.height;
                    ctx.drawImage(img, 0, 0);
                    var im = ctx.getImageData(0, 0, img.width, img.height).data;
                    var arr = new Float32Array(im.buffer);
                    tex = new THREE.DataTexture(arr, img.width, img.height, THREE.LuminanceFormat, THREE.FloatType);
                    tex.premultiplyAlpha = false;
                }
                tex.minFilter = module.filtertypes['nearest'];
                tex.magfilter = module.filtertypes['nearest'];
                tex.needsUpdate = true;
                tex.flipY = false;
                this.shape = [((img.width-1) / this.mosaic[0])-1, ((img.height-1) / this.mosaic[1])-1];
                this.textures.push(tex);

                if (this.textures.length < this.frames) {
                    this.loaded.notify(this.textures.length);
                    loadmosaic(this.textures.length);
                } else {
                    this.loaded.resolve();
                }
            }.bind(this));
            img.src = this.data[this.textures.length];
        }.bind(this);

        loadmosaic(0);
    };
    module.VolumeData.prototype.setFilter = function(interp) {
        for (var i = 0, il = this.textures.length; i < il; i++) {
            this.textures[i].minFilter = module.filtertypes[interp];
            this.textures[i].magFilter = module.filtertypes[interp];
            this.textures[i].needsUpdate = true;
        }
    };
    module.VolumeData.prototype.init = function(uniforms, dim, xfm, filter) {
        uniforms.mosaic.value[dim].set(this.mosaic[0], this.mosaic[1]);
        uniforms.dshape.value[dim].set(this.shape[0], this.shape[1]);
        var volxfm = uniforms.volxfm.value[dim];
        volxfm.set.apply(volxfm, xfm.length != 16 ? xfm[dim] : xfm);
        this.setFilter(filter);
    };

    module.VolumeData.prototype.set = function(uniforms, dim, fframe) {
        if (uniforms.data.value[dim*2] !== this.textures[fframe]) {
            uniforms.data.value[dim*2] = this.textures[fframe];
            if (this.frames > 1) {
                uniforms.data.value[dim*2+1] = this.textures[(fframe+1).mod(this.frames)];
            } else {
                uniforms.data.value[dim*2+1] = null;
            }
        }
    }

    module.VertexData = function(json, images) {
        this.loaded = $.Deferred();
        this.subject = json.subject;
        this.split = json.split;
        this.movie = json.frames > 1;
        this.raw = json.raw;
        this.min = json.min;
        this.max = json.max;
        this.name = json.name;

        this.data = images[json.name];
        this.frames = json.frames;

        this.verts = [];
        NParray.fromURL(this.data[0], function(array) {
            array.loaded.progress(function(available){
                var data = array.view(available-1).data;
                var left = new THREE.BufferAttribute(data.subarray(0, this.split), this.raw?4:1);
                var right = new THREE.BufferAttribute(data.subarray(this.split), this.raw?4:1);
                left.needsUpdate = true;
                right.needsUpdate = true;
                this.verts.push([left, right]);
                this.loaded.notify(available);
            }.bind(this)).done(function(){
                this.loaded.resolve();
            }.bind(this))
        }.bind(this));
    }
    module.VertexData.prototype.init = function(uniforms, dim) {
        //nothing to set...
    }
    module.VertexData.prototype.set = function(uniforms, dim, fframe, dispatch) {
        var name = dim == 0 ? "data0":"data2";
        dispatch({type:"attribute", name:"data"+(2*dim), value:this.verts[fframe]});
        dispatch({type:"attribute", name:"data"+(2*dim+1), value:this.verts[(fframe+1).mod(this.frames)]});
    }

    return module;
}(dataset || {}));