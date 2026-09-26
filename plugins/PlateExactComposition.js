.pragma library
// One Qt-free policy for asynchronous paint transactions and scene readiness.
// All functions are pure: only PlateProgressFace owns Canvas and Image objects.
function empty(epoch,world) {
    return {epoch:epoch,world:world,inFlight:false,pending:false,count:0,
            first:null,last:null,consensus:false};
}
function clone(s) {
    return {epoch:s.epoch,world:s.world,inFlight:s.inFlight,pending:s.pending,
            count:s.count,first:s.first,last:s.last,consensus:s.consensus};
}
function same(a,b) {
    return a!==null && b!==null && a.valid && b.valid
        && a.epoch===b.epoch && a.world===b.world
        && a.from===b.from && a.split===b.split
        && a.prefixSource===b.prefixSource;
}
function newWorld(s,epoch,world) {
    if(s.epoch===epoch && s.world===world) return s;
    var next=empty(epoch,world);
    // A requested paint has no pixels to deliver yet. Qt can drop that
    // request during a scene change; retarget it instead of waiting for a
    // callback that will never arrive. A paint that actually ran still owns
    // its upload slot until delivery, and must remain accounted for.
    next.inFlight=s.inFlight && s.count>0;
    next.pending=true;        // next world must paint after that delivery
    return next;
}
function request(s) {
    var next=clone(s);
    if(next.inFlight) {next.pending=true;return {state:next,start:false};}
    next.inFlight=true;next.pending=false;
    return {state:next,start:true};
}
function enqueue(s) {
    var next=clone(s);
    next.pending=true;
    return next;
}
function painted(s,receipt) {
    var next=clone(s);
    next.inFlight=true; // implicit Qt paints are legitimate requests too
    next.count++;
    if(next.count===1) {next.first=receipt;next.consensus=receipt!==null && receipt.valid;}
    else next.consensus=next.consensus && same(next.first,receipt);
    next.last=receipt;
    return next;
}
function delivered(s,epoch,world) {
    var r=s.last;
    var ok=s.count>0 && s.consensus && r!==null && r.valid
        && s.epoch===epoch && s.world===world
        && r.epoch===epoch && r.world===world;
    var next=empty(s.epoch,s.world);
    next.pending=s.pending || !ok;
    return {state:next,accepted:ok,receipt:ok?r:null};
}
function needsPaint(s) {return s.pending && !s.inFlight;}
function unchanged(s) {
    // Qt can issue onPaint without uploading a new texture. Such a no-op
    // has no delivery to wait for. Never release a real upload's slot.
    return s.count===0 ? empty(s.epoch,s.world) : s;
}
function splitGate(ready,paintEpoch,epoch,paintSplit,split,attached) {
    if(!ready || paintEpoch!==epoch) return false;
    return attached && split!==null && paintSplit>=0
        ? split>=paintSplit : paintSplit===split;
}
function partialComposition(layer,split,ready,splitOk,from,imageReady,prefixSource) {
    // An interval has one owner: either Canvas [0, split], or a matching
    // immutable prefix [0, from] and Canvas [from, split]. A previously
    // visible prefix is not permission to overlay a full Canvas bitmap.
    var vector=layer!==null && split!==null && ready && splitOk && from===0;
    var prefix=layer!==null && split!==null && ready && splitOk && imageReady
        && from>0 && from===layer.prefixSplit && from<=split
        && prefixSource===layer.prefixData;
    return {ready:vector || prefix,vector:vector,prefix:prefix};
}
function presentation(p) {
    // A single decision owns all printed ink. Assets are candidates, never
    // independently visible owners. A delivered tail names the immutable
    // prefix it was painted against; demand changes cannot rename it.
    if(p.full && p.fullReady) return {kind:'full',prefix:'',ready:true};
    var r=p.receipt;
    var delivered=r!==null && r.valid && r.epoch===p.epoch && r.world===p.world;
    // At the exact checkpoint an empty, delivered tail needs no vector
    // geometry. The native prefix alone covers the requested interval.
    if(delivered && r.from===-1 && p.inkless && !p.showTravels
       && p.currentPrefix.ready && p.currentPrefix.from===p.split)
        return {kind:'prefix',prefix:'current',ready:true};
    var complete=p.splitOk && (!p.full || r!==null && r.split>=p.split);
    if(delivered && r.from===0) return {kind:'canvas',prefix:'',ready:complete};
    if(delivered && r.from>0) {
        var current=p.currentPrefix;
        var retained=p.retainedPrefix;
        if(current.ready && current.source===r.prefixSource && current.from===r.from)
            return {kind:'prefix',prefix:'current',ready:complete};
        if(retained.ready && retained.source===r.prefixSource && retained.from===r.from)
            return {kind:'prefix',prefix:'retained',ready:complete};
    }
    if(p.heldFull) return {kind:'heldFull',prefix:'',ready:false};
    return {kind:'preparing',prefix:'',ready:delivered && r.from===-1 && p.inkless && (p.split===0 || p.split===null)};
}
function choosePrefix(current,retained,split) {
    if(split===null || split<=0) return {from:0,source:''};
    if(current.ready && current.from>0 && current.from<=split) return current;
    if(retained.ready && retained.from>0 && retained.from<=split) return retained;
    return {from:0,source:''};
}
function tailPlan(s,d) {
    var reset=s.dirty || d.split<s.split || s.paints>=d.cadence
        || s.source!==d.source || s.view!==d.view
        || s.from!==d.prefix.from
        || (d.prefix.from>0 && s.prefixSource!==d.prefix.source);
    return {reset:reset,from:reset ? (d.prefix.from>0 ? d.prefix.from : -1) : s.split,
            coverage:d.prefix.from>0 ? d.prefix.from : 0};
}
function compositionReady(layer,split,ready,splitOk,from,imageReady,prefixSource) {
    return partialComposition(layer,split,ready,splitOk,from,imageReady,prefixSource).ready;
}
function prefixReady(layer,imageReady,splitOk,from,paintSplit,split,wasShown,inkless,paintedFrom,prefixSource) {
    if(layer===null || !imageReady) return false;
    return partialComposition(layer,split,true,splitOk,from,imageReady,prefixSource).prefix
        || (paintedFrom===-1 && inkless);
}
function exactReady(p) {
    if(!p.available || !p.hasCurrent) return false;
    // Even a zero/unknown split must retire the previous Canvas pixels
    // before the warm picture can yield to the exact presentation.
    if(p.presentationReady===false) return false;
    if(p.full) {
        if(!p.fullImagesReady && !p.fullCanvasReady) return false;
    } else if(p.partial) {
        // A complete full-history Canvas is a legitimate fallback even
        // while the optional prefix is decoding or being replaced.
        if(!p.prefixReady && !p.fullCanvasReady) return false;
    }
    if(p.baseShown && p.basePending) return false;
    return !p.previousPending && !p.nextPending;
}
