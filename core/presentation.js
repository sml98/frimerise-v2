// Transitions and layout measurement share the renderer's seek clock.
(() => {
  let overlay;
  window.__editIn = (kind, t) => {
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.style.cssText="position:fixed;inset:0;pointer-events:none;z-index:10000";
      document.body.append(overlay);
    }
    const p=Math.min(1,t/.30);
    const amount=(1-p)**3;
    const subject=document.querySelector("[data-edit-stage]") || document.querySelector("main");
    if (subject && ["zoom_punch","whip_pan"].includes(kind)) {
      subject.style.translate=kind==="whip_pan" ? `${amount*1.6}vw` : "none";
      subject.style.scale=String(1+amount*(kind==="zoom_punch" ? .055 : .035));
    }
    overlay.style.opacity=String(amount);
    if(kind==="flash_cut") overlay.style.background="rgba(245,205,150,.4)";
    else if(kind==="paper_rip") {
      const x = 28*amount-4;
      overlay.style.background="#b59774";
      overlay.style.clipPath=`polygon(0 0,${x}% 0,${x-2}% 14%,${x+1}% 28%,${x-2}% 45%,${x+1.5}% 71%,${x-1}% 91%,${x}% 100%,0 100%)`;
    } else overlay.style.background="transparent";
  };
  const channels = color => color.match(/[\d.]+/g).map(Number);
  const luminance = rgb => rgb.map(value => {
    const s=value/255;
    return s<=.04045 ? s/12.92 : ((s+.055)/1.055)**2.4;
  }).reduce((sum,value,i)=>sum+value*[.2126,.7152,.0722][i],0);

  window.__captionContrast = () => {
    const caption=document.querySelector("[data-qc-text='legenda']");
    const surface=caption?.querySelector("[data-qc-caption-surface]");
    if (!caption?.textContent.trim() || !caption.hasAttribute("data-qc-require-caption-surface")) return null;
    if (!surface) return {ratio:0,passed:false};
    const style=getComputedStyle(surface);
    const [r,g,b,alpha=1]=channels(style.backgroundColor);
    const brightest=luminance([r,g,b].map(value=>alpha*value+(1-alpha)*255));
    const analyzed=[surface,...surface.children];
    const ratio=Math.min(...analyzed.map(node=>
      (luminance(channels(getComputedStyle(node).color).slice(0,3))+.05)/(brightest+.05)
    ));
    return {ratio,passed:alpha>=.84 && style.backdropFilter.includes("blur(4px)") && ratio>=4.5};
  };

  window.__layoutViolations = () => {
    const w=innerWidth, h=innerHeight;
    const safe={left:.052*w,right:.948*w,top:.05*h,bottom:(h>w?.86:.97)*h};
    const boxes=(selector, text)=>Array.from(document.querySelectorAll(selector))
      .filter(e=> (!text || e.textContent.trim()) && [...ancestors(e)].every(a=>{
        const s=getComputedStyle(a);
        return s.display!=="none" && s.visibility!=="hidden" && +s.opacity>.04;
      }))
      .flatMap(e=>{
        const id=e.getAttribute(text?"data-qc-text":"data-qc-image");
        if(!text) return [{id,r:e.getBoundingClientRect()}];
        const walker=document.createTreeWalker(e,NodeFilter.SHOW_TEXT), result=[];
        for(let node=walker.nextNode();node;node=walker.nextNode()){
          if(!node.textContent.trim()) continue;
          const glyphs=document.createRange();
          glyphs.selectNodeContents(node);
          for(const r of glyphs.getClientRects()) result.push({id,r});
        }
        return result;
      })
      .filter(({r})=>r.width>1 && r.height>1);
    function* ancestors(element) {for(let e=element;e;e=e.parentElement) yield e;}
    function overlap(a,b) {
      return Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2;
    }
    const text=boxes("[data-qc-text]",true), graphics=boxes("[data-qc-image]",false), issues=[];
    for (const {id,r} of text) {
      if (r.left<safe.left-1 || r.right>safe.right+1 || r.top<safe.top-1 || r.bottom>safe.bottom+1)
        issues.push({type:"unsafe_text",text:id});
      for (const image of graphics) if (overlap(r,image.r)) issues.push({type:"collision",text:id,image:image.id});
    }
    if (window.__captionContrast()?.passed === false) issues.push({type:"caption_contrast",text:"legenda"});
    return issues;
  };
})();
