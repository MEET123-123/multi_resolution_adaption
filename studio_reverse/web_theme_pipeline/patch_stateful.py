from pathlib import Path
p=Path('studio_reverse/web_theme_pipeline/interactive.py')
s=p.read_text(encoding='utf8')
for a,b in [('   x,y,_,_=position(name,dev)','   x,y,_,_=state_position(name,dev,state)'),('   x,y,w,h=position(name,dev)','   x,y,w,h=state_position(name,dev,state)')]:
 if a not in s and b not in s:raise ValueError('Missing snippet: '+a)
 s=s.replace(a,b)
a='     sn,page,weather,drawer,night,trace=st\n     target='
b="""     sn,page,weather,drawer,night,trace=st
     answer={'schema':'multires-layout/1','groups':{
      'g'+str(j):[round(1000*v[0]/w,3),round(1000*v[1]/h,3),round(1000*v[2]/w,3),round(1000*v[3]/h,3)]
      for j,v in enumerate(state_position(name,dev,st) for name in BOX)}}
     target="""
if a not in s and b not in s:raise ValueError('Cannot find state loop')
s=s.replace(a,b)
p.write_text(s,encoding='utf8')
print('Patched',p)
