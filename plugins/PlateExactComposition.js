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
        && a.from===b.from && a.split===b.split;
}
function newWorld(s,epoch,world) {
    if(s.epoch===epoch && s.world===world) return s;
    var next=empty(epoch,world);
    next.inFlight=s.inFlight;  // old render's delivery still owns this slot
    next.pending=true;        // next world must paint after that delivery
    return next;
}
function request(s) {
    var next=clone(s);
    if(next.inFlight) {next.pending=true;return {state:next,start:false};}
    next.inFlight=true;next.pending=false;
    return {state:next,start:true};
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
function splitGate(ready,paintEpoch,epoch,paintSplit,split,attached) {
    if(!ready || paintEpoch!==epoch) return false;
    return attached && split!==null && paintSplit>=0
        ? split>=paintSplit : paintSplit===split;
}
function compositionReady(layer,split,ready,splitOk,from) {
    return layer!==null && split!==null && ready && splitOk
        && (from===0 || from===layer.prefixSplit);
}
function prefixReady(layer,imageReady,splitOk,from,paintSplit,split,wasShown,inkless,paintedFrom) {
    if(layer===null || !imageReady) return false;
    // First show over a full vector bitmap would double-render the history.
    return (splitOk && (from===layer.prefixSplit
        || (from===0 && wasShown && paintSplit===split)))
        || (paintedFrom===-1 && inkless);
}
function exactReady(p) {
    if(!p.available || !p.hasCurrent) return false;
    if(p.full) {
        if(p.fullPending || (p.travelsShown && p.travelsPending)) return false;
    } else if(p.partial) {
        if(p.prefixUsable ? !p.prefixReady : !p.fullCanvasReady) return false;
        if(p.baseShown && p.basePending) return false;
    }
    return !p.previousPending && !p.nextPending;
}
