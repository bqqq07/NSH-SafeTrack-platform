(function(){
  const achInputs = document.querySelectorAll('input.ach');   // % للأهداف
  const rateInputs = document.querySelectorAll('input.rate'); // تقييم الأداء

  function calc(){
    // حساب الأهداف
    let t_sum = 0, t_count = 0;
    achInputs.forEach(inp=>{
      if(inp.value){ // فقط لو فيه قيمة
        const v = Math.max(0, Math.min(100, parseFloat(inp.value || 0)));
        t_sum += v;
        t_count++;
      }
    });
    // متوسط الأهداف
    let t_avg = t_count > 0 ? (t_sum / t_count) : 0;
    let t_score = (t_avg / 100) * 50; // وزنه 50%

    // حساب الأداء
    let p_sum = 0, p_count = 0;
    rateInputs.forEach(r=>{
      if(r.checked){ // يحسب اللي متعلم فقط
        p_sum += parseInt(r.value);
        p_count++;
      }
    });
    let p_avg = p_count > 0 ? (p_sum / p_count) : 0;
    let p_score = (p_avg / 5) * 50; // وزنه 50%

    // المجموع النهائي
    const total = Math.round((t_score + p_score) * 100) / 100;

    // التصنيف
    let band = "Needs Improvement";
    if(total >= 90) band = "Excellent";
    else if(total >= 80) band = "Good";
    else if(total >= 70) band = "Satisfactory";

    // تحديث القيم في الصفحة
    document.getElementById('sumTargets').textContent = t_score.toFixed(2);
    document.getElementById('sumPerf').textContent = p_score.toFixed(2);
    document.getElementById('sumTotal').textContent = total.toFixed(2);
    document.getElementById('sumBand').textContent = band;
  }

  achInputs.forEach(i => i.addEventListener('input', calc));
  rateInputs.forEach(i => i.addEventListener('change', calc));
  calc(); // تشغيل أولي
})();
