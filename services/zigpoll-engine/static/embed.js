(() => {
  const id = window.ZIGPOLL_PUBLIC_ID;
  const form = document.querySelector('#survey-form');
  const message = document.querySelector('#message');
  const label = (text) => { const node = document.createElement('label'); node.className = 'question'; node.textContent = text; return node; };
  const fail = (text) => { message.textContent = text; message.className = 'error'; };
  fetch(`/api/public/${encodeURIComponent(id)}`).then(async (response) => {
    const survey = await response.json();
    if (!response.ok) throw new Error(survey.detail || 'Survey is unavailable');
    document.querySelector('#title').textContent = survey.title;
    document.querySelector('#description').textContent = survey.description;
    survey.questions.forEach((question) => {
      const wrap = label(question.label + (question.required ? ' *' : ''));
      let input;
      if (question.type === 'single_choice') {
        input = document.createElement('select'); input.name = question.id; input.required = question.required;
        const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Choose one'; input.append(empty);
        question.options.forEach((value) => { const option = document.createElement('option'); option.value = value; option.textContent = value; input.append(option); });
      } else if (question.type === 'multi_choice') {
        input = document.createElement('div'); input.className = 'choices';
        question.options.forEach((value) => { const item = document.createElement('label'); item.className = 'choice'; const check = document.createElement('input'); check.type='checkbox'; check.name=question.id; check.value=value; item.append(check, document.createTextNode(value)); input.append(item); });
      } else if (question.type === 'rating') {
        input = document.createElement('select'); input.name = question.id; input.required = question.required;
        const empty = document.createElement('option'); empty.value=''; empty.textContent='Select a rating'; input.append(empty);
        for (let value=question.min_value; value<=question.max_value; value++) { const option=document.createElement('option'); option.value=String(value); option.textContent=String(value); input.append(option); }
      } else {
        input = document.createElement('textarea'); input.name=question.id; input.maxLength=4000; input.rows=3; input.required=question.required;
      }
      wrap.append(input); form.append(wrap);
    });
    const submit = document.createElement('button'); submit.type='submit'; submit.className='submit'; submit.textContent='Submit response'; form.append(submit);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); message.textContent='';
      const answers = {};
      survey.questions.forEach((question) => {
        if (question.type === 'multi_choice') answers[question.id] = [...form.querySelectorAll(`input[name="${CSS.escape(question.id)}"]:checked`)].map((node)=>node.value);
        else if (question.type === 'rating') { const value=form.elements[question.id].value; if(value) answers[question.id]=Number(value); }
        else { const value=form.elements[question.id].value; if(value) answers[question.id]=value; }
      });
      try {
        const response=await fetch(`/api/public/${encodeURIComponent(id)}/responses`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})});
        const data=await response.json(); if(!response.ok) throw new Error(data.detail || 'Response could not be submitted');
        form.replaceChildren(); message.textContent='Thank you. Your response has been recorded.'; message.className='success';
      } catch(error) { fail(error.message); }
    });
  }).catch((error)=>fail(error.message));
})();
