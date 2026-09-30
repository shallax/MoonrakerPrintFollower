// Cura SimulationView gradient equations, shared by Canvas and the legend.
.pragma library
function key(mode) { return ({2:"speed",3:"height",4:"width",5:"flow"})[mode]; }
function gradient(mode, value, bounds) {
    var lo=bounds[0], hi=bounds[1], constant=Math.abs(hi-lo)<0.0001;
    var v=constant?0.5:Math.max(0,Math.min(1,(value-lo)/(hi-lo)));
    if(mode===3) return Qt.rgba(Math.max(0,Math.min(1,4*v-2)),v>0.75?v:Math.min(1.5*v,0.75),0.75-Math.abs(0.25-v),1);
    if(mode===5) { var t=constant?0:2*v-1; return Qt.rgba(Math.max(0,Math.min(1,1.5-Math.abs(2*t-1))),Math.max(0,Math.min(1,1.5-Math.abs(2*t))),Math.max(0,Math.min(1,1.5-Math.abs(2*t+1))),1); }
    return Qt.rgba(v,v>0.375?0.5:1-Math.abs(1-4*v),Math.max(1-4*v,0),1);
}
function colour(layer, name, motion, scheme) {
    var mode=scheme.mode===undefined?1:scheme.mode;
    if(mode===1||name.indexOf("TRAVEL")===0) return scheme.classes&&scheme.classes[name]?scheme.classes[name]:"#888888";
    if(mode===0) { var tools=layer.tools||[], palette=scheme.materials||["#888888"]; return palette[tools[motion]||0]||palette[0]; }
    var speeds=layer.speeds||[], widths=layer.widths||[], speed=speeds[motion]||0, width=widths[motion]>0?widths[motion]:0.4, height=layer.layerHeight||0.2;
    var value=mode===2?speed:(mode===3?height:(mode===4?width:width*height*speed));
    return gradient(mode,value,(layer.colourRanges||{})[key(mode)]||[0,0]);
}
