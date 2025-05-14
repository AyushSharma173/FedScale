import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { useNavigate } from 'react-router-dom'

export default function MainPage() {
  const [expts, setExpts] = useState([])
  const navigate = useNavigate()
  const [form, setForm] = useState({
    name: '',
    num_executors: 1,
    num_clients: 4,
    gradient_policy: 'FedAvg',
    experiment_mode: 'sync',
    backend: 'gloo',
    engine: 'PyTorch',
    model_zoo: 'vision',
    model: 'shufflenet_v2_x2_0',
    data_set: 'CIFAR10',
    data_dir: '/data',
    input_shape: '3,32,32',
    output_dim: 10,
    num_classes: 10,
    embedding_file: '',
    rounds: 5,
    local_steps: 1,
    batch_size: 8,
    test_bsz: 8,
    learning_rate: 0.01,
    min_learning_rate: 0.001,
    decay_factor: 0.1,
    decay_round: 3,
    clip_bound: 1.0,
    eval_interval: 1,
    dump_epoch: 5,
  })

  useEffect(() => {
    axios.get('http://localhost:8000/experiments')
      .then(r => setExpts(r.data))
      .catch(console.error)
  }, [])

  const handleChange = e => {
    const { name, value } = e.target
    setForm(f => ({ ...f, [name]: (e.target.type==='number'? +value : value) }))
  }

  const start = async e => {
    // e.preventDefault()
    // await axios.post('http://localhost:8000/experiments', form)
    // const r = await axios.get('http://localhost:8000/experiments')
    // setExpts(r.data)

    e.preventDefault();
    
    // fire off the POST and grab the new experiment
    const { data: newExp } = await axios.post(
        'http://localhost:8000/experiments',
        form
    )
    // immediately navigate to its detail page
    navigate(`/experiments/${newExp.id}`)
  }

  return (
    <div className="dashboard">
      <header><h1>FedScale Control Center</h1></header>
      <section className="controls">
        <h2>Start New Experiment</h2>
        <form onSubmit={start}>
          <div className="grid-2">
            {Object.entries(form).map(([k,v]) => (
              <label key={k}>
                {k.replace(/_/g,' ')}:
                <input
                  name={k}
                  value={v}
                  onChange={handleChange}
                  type={typeof v === 'number' ? 'number' : 'text'}
                  style={{ width: '100%' }}
                />
              </label>
            ))}
          </div>
          <button type="submit">Start Experiment</button>
        </form>
      </section>

      <section className="controls">
        <h2>Past Experiments</h2>
        <table className="exp-list">
          <thead>
            <tr><th>Name</th><th>ID</th><th>Clients</th><th>Execs</th><th>Status</th></tr>
          </thead>
          <tbody>
            {expts.map(e => (
              <tr key={e.id} onClick={()=>navigate(`/experiments/${e.id}`)} style={{cursor:'pointer'}}>
                <td>{e.name}</td>
                <td className="monospace">{e.id.slice(0,8)}…</td>
                <td>{e.num_clients}</td>
                <td>{e.num_executors}</td>
                <td>{e.running? '🟢':'🔴'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  )
}
